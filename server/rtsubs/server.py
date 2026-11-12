"""Local HTTP API used by the browser extension for Real-Time Japanese -> Vietnamese Subtitles.

POST /jobs            {urls, duration?, referer?, cookie?, start?} -> {job_id}
GET  /jobs/{id}       -> status + per-chunk readiness + subtitles
POST /jobs/{id}/seek  {t} -> prioritize chunks at or right after current video playback time t
"""
from __future__ import annotations

import subprocess
import sys
import threading
import urllib.parse
import uuid

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from .chunker import Chunk, plan_chunks, speech_timestamps
from .config import Settings
from .pipeline import HLS_INPUT_ARGS, Engine, HlsPlaylist, VideoSession, decode
from .translate import Translator

S = Settings()
app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


@app.middleware("http")
async def private_network(request, call_next):  # Chrome/Edge Private Network Access preflight
    response = await call_next(request)
    response.headers["Access-Control-Allow-Private-Network"] = "true"
    return response


def _same_domain(url_a: str | None, url_b: str | None) -> bool:
    if not url_a or not url_b:
        return False
    try:
        ha = urllib.parse.urlparse(url_a).hostname or ""
        hb = urllib.parse.urlparse(url_b).hostname or ""
        return ha.lower().lstrip("www.") == hb.lower().lstrip("www.")
    except Exception:
        return False


def _origin_of(referer: str | None) -> str | None:
    if not referer:
        return None
    try:
        p = urllib.parse.urlparse(referer)
        if p.scheme and p.netloc:
            return f"{p.scheme}://{p.netloc}"
    except Exception:
        pass
    return None


def _build_http_headers(
    target_url: str, referer: str | None, cookie: str | None = None, user_agent: str | None = None
) -> dict[str, str]:
    ua = (
        user_agent
        or "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
    )
    headers: dict[str, str] = {
        "User-Agent": ua,
        "Accept": "*/*",
    }
    if referer:
        headers["Referer"] = referer
        origin = _origin_of(referer)
        if origin:
            headers["Origin"] = origin
    # Only send cookies if target_url is on the same domain as the page (sending missav.ws cookies to surrit.com triggers Cloudflare 403)
    if cookie and _same_domain(target_url, referer):
        headers["Cookie"] = cookie
    return headers


def _build_ffmpeg_headers(
    target_url: str, referer: str | None, cookie: str | None = None, user_agent: str | None = None
) -> list[str]:
    headers = _build_http_headers(target_url, referer, cookie, user_agent)
    hdrs = [f"{k}: {v}" for k, v in headers.items()]
    return ["-headers", "\r\n".join(hdrs) + "\r\n"]


def _is_hls_manifest(url: str) -> bool:
    u = url.lower().split("?")[0]
    return u.endswith(".m3u8") or ".m3u8" in url.lower() or "mpegurl" in url.lower()


def _is_master_hls(url: str) -> bool:
    u = url.lower().split("?")[0]
    return u.endswith("playlist.m3u8") or u.endswith("master.m3u8") or u.endswith("index.m3u8")


def _is_short_segment(url: str) -> bool:
    u = url.lower().split("?")[0]
    return u.endswith(".ts") or u.endswith(".m4s") or u.endswith(".jpeg") or u.endswith(".jpg")


def _probe_duration(url: str, header_args: list[str]) -> float | None:
    """Duration of a non-HLS URL that carries an audio/media stream."""
    extra = HLS_INPUT_ARGS if _is_hls_manifest(url) else []
    base = ["ffprobe", "-v", "error", *header_args, *extra]
    try:
        audio = subprocess.run(
            base + ["-select_streams", "a", "-show_entries", "stream=codec_type", "-of", "csv=p=0", url],
            capture_output=True,
            text=True,
            timeout=15,
        ).stdout.strip()
        if not audio:
            return None
        out = subprocess.run(
            base + ["-show_entries", "format=duration", "-of", "default=nw=1:nk=1", url],
            capture_output=True,
            text=True,
            timeout=15,
        ).stdout.strip()
        try:
            return float(out)
        except ValueError:
            return 0.0
    except Exception:
        return None


def pick_url(
    urls: list[str],
    duration: float,
    referer: str | None,
    cookie: str | None,
    user_agent: str | None,
    exact: bool = False,
) -> tuple[str, float, list[str], HlsPlaylist | None]:
    """Select the best media URL.
    For .m3u8 HLS playlists, builds an HlsPlaylist in Python so segments (even disguised as .jpeg/.png)
    are downloaded directly in Python and piped to FFmpeg."""
    ordered = sorted(
        urls,
        key=lambda u: (
            0 if _is_master_hls(u) else (1 if _is_hls_manifest(u) else (3 if _is_short_segment(u) else 2))
        ),
    )

    best, best_d, best_hdrs, best_err = None, 0.0, [], 1e9
    for u in ordered:
        http_hdrs = _build_http_headers(u, referer, cookie, user_agent)
        hdr_args = _build_ffmpeg_headers(u, referer, cookie, user_agent)

        if _is_hls_manifest(u):
            pl = HlsPlaylist.from_url(u, http_hdrs)
            if pl is not None and len(pl.segments) > 0:
                resolved_dur = pl.duration if pl.duration > 0 else (duration if duration > 0 else 3600.0)
                print(
                    f"[pick] HLS playlist ready: {len(pl.segments)} segments, "
                    f"duration={resolved_dur:.1f}s url={pl.media_url[:100]}",
                    flush=True,
                )
                return pl.media_url, resolved_dur, hdr_args, pl

        d = _probe_duration(u, hdr_args)
        print(f"[pick] duration={d} exact={exact} url={u[:110]}", flush=True)
        if d is None:
            continue
        if duration > 25.0 and 0 < d < 15.0 and _is_short_segment(u):
            print(f"[pick] skipping short segment ({d:.1f}s vs expected {duration:.1f}s)", flush=True)
            continue
        if exact and (duration <= 0 or d == 0.0 or abs(d - duration) <= max(5.0, duration * 0.15)):
            return u, (d if d > 0 else duration), hdr_args, None
        err = abs(d - duration) if (duration > 0 and d > 0) else 0.0
        if err < best_err:
            best, best_d, best_hdrs, best_err = u, (d if d > 0 else duration), hdr_args, err
        if duration > 0 and d > 0 and err < 1.0:
            break

    if best is None:
        raise RuntimeError(
            "No reachable media stream with audio found (check if site uses DRM or only exposed a .ts segment)"
        )
    return best, best_d, best_hdrs, None


class Job:
    def __init__(
        self,
        urls: list[str],
        duration: float,
        referer: str | None,
        cookie: str | None = None,
        user_agent: str | None = None,
        exact: bool = False,
        start: float = 0.0,
        transcript: list[dict] | None = None,
        transcript_lang: str | None = None,
    ):
        self.id = uuid.uuid4().hex[:8]
        self.urls = urls
        self.expected = duration
        self.referer = referer
        self.cookie = cookie
        self.user_agent = user_agent
        self.exact = exact
        self.url = ""
        self.state = "loading"  # loading | running | done | error | cancelled
        self.error = ""
        self.duration = 0.0
        self.chunks: list[Chunk] = []
        self.done_chunks: set[int] = set()
        self.subs: dict[int, list[dict]] = {}
        self.timing: dict[int, dict] = {}
        self.pointer = 0
        self.initial_start = start
        self.transcript = transcript
        self.transcript_lang = transcript_lang
        self.session: VideoSession | None = None
        self.cancel = threading.Event()
        self.lock = threading.Lock()

    def next_chunk(self) -> int | None:
        todo = [c.index for c in self.chunks if c.index not in self.done_chunks]
        if not todo:
            return None
        return next((i for i in todo if i >= self.pointer), todo[0])


engine: Engine | None = None
jobs: dict[str, Job] = {}
current: Job | None = None
gpu_lock = threading.Lock()


def _engine() -> Engine:
    global engine
    if engine is None:
        engine = Engine(S)
    return engine


def _uniform_chunks(duration: float, s: Settings) -> list[Chunk]:
    """Create clean ~10s chunks immediately when streaming a long .m3u8 so chunk 0 starts without waiting to download the whole movie."""
    chunks: list[Chunk] = []
    start = 0.0
    step = s.chunk_target_sec
    while start < duration - 1e-3:
        end = min(duration, start + step)
        if duration - end < 3.0:
            end = duration
        chunks.append(Chunk(len(chunks), round(start, 2), round(end, 2)))
        start = end
    return chunks


def run_job(job: Job) -> None:
    try:
        if job.cancel.is_set():
            job.state = "cancelled"
            return

        work = S.workspace / "live" / job.id
        work.mkdir(parents=True, exist_ok=True)

        is_youtube = any("youtube.com" in u or "youtu.be" in u for u in job.urls) or (
            "youtube.com" in (job.referer or "")
        )
        a16 = None
        stream_url = None
        hls_playlist: HlsPlaylist | None = None
        header_args: list[str] = []

        if is_youtube:
            yt_url = next((u for u in job.urls if "youtube.com" in u or "youtu.be" in u), job.urls[0])
            job.url = yt_url
            print(f"[job {job.id}] Downloading YouTube audio via yt-dlp: {yt_url}", flush=True)

            def _ydl_hook(d):
                if job.cancel.is_set():
                    import yt_dlp.utils

                    raise yt_dlp.utils.DownloadCancelled("Job cancelled by client")

            import yt_dlp

            ydl_opts = {
                "format": "ba/b",
                "outtmpl": str(work / "source.%(ext)s"),
                "quiet": True,
                "no_warnings": True,
                "noplaylist": True,
                "progress_hooks": [_ydl_hook],
            }
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([yt_url])

            if job.cancel.is_set():
                job.state = "cancelled"
                return

            src_files = [f for f in work.glob("source.*") if not f.name.endswith(".part")]
            if not src_files:
                raise RuntimeError("Failed to locate downloaded YouTube audio file")
            src_audio = src_files[0]
            print(f"[job {job.id}] YouTube audio ready: {src_audio.name}", flush=True)
            a16 = decode(src_audio, 16000, 1)
            job.duration = len(a16) / 16000
        else:
            picked_url, probed_dur, header_args, hls_playlist = pick_url(
                job.urls, job.expected, job.referer, job.cookie, job.user_agent, job.exact
            )
            job.url = picked_url
            if job.cancel.is_set():
                job.state = "cancelled"
                return

            effective_dur = probed_dur or job.expected or 3600.0
            if hls_playlist is not None:
                print(
                    f"[job {job.id}] Using Python HLS Segment Streamer for {picked_url[:90]} (duration={effective_dur:.1f}s)",
                    flush=True,
                )
                job.duration = effective_dur
            elif (_is_hls_manifest(picked_url) or effective_dur > 240.0) and effective_dur > 0:
                print(
                    f"[job {job.id}] Using real-time windowed stream mode for {picked_url[:90]} (duration={effective_dur:.1f}s)",
                    flush=True,
                )
                stream_url = picked_url
                job.duration = effective_dur
            else:
                a16 = decode(job.url, 16000, 1, header_args)
                job.duration = len(a16) / 16000

        if job.cancel.is_set():
            job.state = "cancelled"
            return

        if job.transcript and len(job.transcript) > 0:
            print(
                f"[job {job.id}] Using pre-existing transcript ({len(job.transcript)} cues, lang={job.transcript_lang})",
                flush=True,
            )
            speech = [
                (float(c["start"]), float(c["end"]))
                for c in job.transcript
                if float(c.get("end", 0)) > float(c.get("start", 0))
            ]
            job.chunks = plan_chunks(job.duration, speech, S) if speech else _uniform_chunks(job.duration, S)
        elif a16 is not None:
            speech = speech_timestamps(a16)
            job.chunks = plan_chunks(job.duration, speech, S)
        else:
            job.chunks = _uniform_chunks(job.duration, S)

        if job.initial_start > 0:
            for c in job.chunks:
                if c.start <= job.initial_start < c.end:
                    job.pointer = c.index
                    break

        if job.cancel.is_set():
            job.state = "cancelled"
            return

        with gpu_lock:
            if job.cancel.is_set():
                job.state = "cancelled"
                return
            eng, tr = _engine(), Translator(S)

        session = VideoSession(
            eng,
            tr,
            a16,
            job.chunks,
            transcript=job.transcript,
            transcript_lang=job.transcript_lang,
            stream_url=stream_url,
            stream_args=header_args,
            hls_playlist=hls_playlist,
        )
        job.session = session
        job.state = "running"

        with gpu_lock:
            if job.cancel.is_set():
                job.state = "cancelled"
                return
            session.warm_up(start_index=job.pointer)

        while not job.cancel.is_set():
            i = job.next_chunk()
            if i is None or job.cancel.is_set():
                break
            c = job.chunks[i]
            with gpu_lock:
                if job.cancel.is_set():
                    break
                subs, timing = session.process(i, cancel_event=job.cancel)
            if job.cancel.is_set():
                break
            with job.lock:
                job.done_chunks.add(i)
                job.subs[i] = subs
                job.timing[i] = timing
            dur = max(0.1, c.end - c.start)
            print(
                f"[job {job.id}] DONE chunk {i}/{len(job.chunks)} ({c.start:.1f}s-{c.end:.1f}s) -> "
                f"{len(subs)} Vietnamese sub(s) ready | timing={timing} RTF={timing.get('total', 0.0) / dur:.2f}\n"
                f"------------------------------------------------------------",
                flush=True,
            )

        session.cancel.set()
        job.state = "cancelled" if job.cancel.is_set() else "done"
        print(f"[job {job.id}] finished with state: {job.state}", flush=True)
    except Exception as exc:
        if job.cancel.is_set():
            job.state = "cancelled"
            print(f"[job {job.id}] stopped cleanly on cancel: {exc}", flush=True)
            return
        job.state, job.error = "error", str(exc)
        print(f"[job {job.id}] error: {exc}", flush=True)
    finally:
        if job.session:
            job.session.cancel.set()


class NewJob(BaseModel):
    urls: list[str]
    exact: bool = False
    duration: float = 0.0
    referer: str | None = None
    cookie: str | None = None
    user_agent: str | None = None
    start: float = 0.0
    transcript: list[dict] | None = None
    transcript_lang: str | None = None


class Seek(BaseModel):
    t: float


@app.post("/jobs")
def create_job(req: NewJob):
    global current
    if current and current.state in ("loading", "running"):
        current.cancel.set()
        if current.session:
            current.session.cancel.set()
        current.state = "cancelled"
    job = Job(
        req.urls,
        req.duration,
        req.referer,
        cookie=req.cookie,
        user_agent=req.user_agent,
        exact=req.exact,
        start=req.start,
        transcript=req.transcript,
        transcript_lang=req.transcript_lang,
    )
    jobs[job.id] = current = job
    threading.Thread(target=run_job, args=(job,), daemon=True).start()
    return {"job_id": job.id}


@app.post("/jobs/{job_id}/stop")
@app.delete("/jobs/{job_id}")
def stop_job(job_id: str):
    if job_id not in jobs:
        return {"ok": False, "error": "unknown job"}
    j = jobs[job_id]
    j.cancel.set()
    if j.session:
        j.session.cancel.set()
    j.state = "cancelled"
    print(f"[server] Job {job_id} cancelled by client stop request", flush=True)
    return {"ok": True, "job_id": job_id, "state": "cancelled"}


@app.post("/jobs/stop-all")
def stop_all_jobs():
    count = 0
    for j in jobs.values():
        if j.state in ("loading", "running"):
            j.cancel.set()
            if j.session:
                j.session.cancel.set()
            j.state = "cancelled"
            count += 1
    print(f"[server] Cancelled {count} active jobs", flush=True)
    return {"ok": True, "cancelled": count}


def _job(job_id: str) -> Job:
    if job_id not in jobs:
        raise HTTPException(404, "unknown job")
    return jobs[job_id]


@app.get("/jobs/{job_id}")
def status(job_id: str):
    j = _job(job_id)
    with j.lock:
        return {
            "state": j.state,
            "error": j.error,
            "duration": j.duration,
            "chunks": [
                {"i": c.index, "start": c.start, "end": c.end, "ready": c.index in j.done_chunks} for c in j.chunks
            ],
            "subs": [s for i in sorted(j.subs) for s in j.subs[i]],
        }


@app.post("/jobs/{job_id}/seek")
def seek(job_id: str, req: Seek):
    j = _job(job_id)
    target = next((c.index for c in j.chunks if c.start <= req.t < c.end), None)
    if target is not None:
        j.pointer = target
        if j.session:
            j.session.play_time = req.t
    return {"pointer": j.pointer}


@app.get("/health")
def health():
    return {"ok": True, "engine_loaded": engine is not None, "mode": "subtitle_only"}


def main() -> None:
    masked_key = (
        f"{S.api_key[:9]}...{S.api_key[-4:]}"
        if (S.api_key and S.api_key != "dummy" and len(S.api_key) > 14)
        else "NOT SET (Check .env!)"
    )
    print("============================================================", flush=True)
    print(f"[Config] Source Language   : {S.source_language} (Whisper model: {S.whisper_model})", flush=True)
    print(f"[Config] Translation API   : {S.base_url}", flush=True)
    print(f"[Config] Translation Model : {S.model}", flush=True)
    print(f"[Config] OpenRouter Key    : {masked_key}", flush=True)
    print("============================================================", flush=True)
    _engine()  # load Whisper model up front so the first request is fast
    print("[server] Whisper ASR ready (Subtitle-Only mode)", flush=True)
    uvicorn.run(app, host="127.0.0.1", port=8765, log_level="warning")


if __name__ == "__main__":
    main()
