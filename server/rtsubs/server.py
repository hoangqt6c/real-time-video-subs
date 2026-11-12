"""Local HTTP API used by the browser extension for Real-Time Japanese -> Vietnamese Subtitles.

Features:
- Two-Tier Adaptive Chunking:
  * Tier 1 (Instant Playhead Chunk): 15-second chunk on initial start or seek (~1.5s latency).
  * Tier 2 (Mega-Batch): 150-second (2.5-minute) contiguous batches thereafter, sending up to 50 lines
    per single OpenRouter call (`max_tokens=8192`) to cut API requests by 10x-15x.
- Persistent subtitle & Whisper ASR caching keyed by Video Code (e.g. MEYD-916) in workspace/cache/<CODE>.json
  plus browser localStorage sync.
- Scrapes page title & plot summary from the video page (e.g. missav) for rich 18+ context.
- Male/Female voice pitch detection so "anh/em/chị" pronouns match the speaker's actual voice.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import threading
import time
import urllib.parse
import uuid
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from .asr import Seg
from .chunker import Chunk, plan_chunks, speech_timestamps
from .config import Settings
from .pipeline import HLS_INPUT_ARGS, Engine, HlsPlaylist, VideoSession, decode
from .translate import Translator

S = Settings()
CACHE_DIR = S.workspace / "cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)
_cache_io_lock = threading.Lock()

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


@app.middleware("http")
async def private_network(request, call_next):  # Chrome/Edge Private Network Access preflight
    response = await call_next(request)
    response.headers["Access-Control-Allow-Private-Network"] = "true"
    return response


_CODE_RE = re.compile(r"\b(FC2-PPV-\d{5,8}|[A-Z]{2,6}-\d{2,6})\b", re.IGNORECASE)


def _extract_or_sanitize_code(video_code: str | None, page_title: str | None, referer: str | None) -> str:
    """Extract canonical movie code like MEYD-916 or STARS-359 from code, title, or URL."""
    for candidate in (video_code or "", page_title or "", referer or ""):
        m = _CODE_RE.search(candidate)
        if m:
            return m.group(1).upper()
    if video_code and video_code.strip():
        clean = re.sub(r"[^A-Za-z0-9_-]+", "_", video_code.strip())[:64].strip("_")
        if clean:
            return clean.upper()
    if referer:
        try:
            p = urllib.parse.urlparse(referer)
            slug = p.path.rstrip("/").split("/")[-1]
            clean = re.sub(r"[^A-Za-z0-9_-]+", "_", slug)[:64].strip("_")
            if clean:
                return clean.upper()
        except Exception:
            pass
    return ""


def _cache_file(code: str) -> Path | None:
    if not code:
        return None
    safe = re.sub(r"[^A-Za-z0-9_-]+", "_", code)[:80]
    return CACHE_DIR / f"{safe}.json"


def _load_video_cache(code: str) -> dict:
    path = _cache_file(code)
    if not path or not path.is_file():
        return {}
    with _cache_io_lock:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"[Cache] Warning: could not read {path.name}: {e}", flush=True)
            return {}


def _save_video_cache(job: Job) -> None:
    if not job.video_code:
        return
    path = _cache_file(job.video_code)
    if not path:
        return
    with job.lock:
        done_rivers = [
            {"start": round(job.chunks[i].start, 2), "end": round(job.chunks[i].end, 2)}
            for i in sorted(job.done_chunks)
            if 0 <= i < len(job.chunks)
        ]
        done_list = sorted(job.done_chunks)
        subs_dict = {str(k): v for k, v in sorted(job.subs.items())}
        asr_dict: dict[str, list[dict]] = {}
        if job.session:
            for idx, seg_list in job.session._segs.items():
                asr_dict[str(idx)] = [
                    {
                        "start": round(g.start, 2),
                        "end": round(g.end, 2),
                        "zh": g.zh,
                        "vi": g.vi,
                        "gender": g.gender,
                        "f0": g.f0,
                    }
                    for g in seg_list
                ]
        bible = job.session.tr.bible if (job.session and job.session.tr) else {}

    payload = {
        "video_code": job.video_code,
        "page_title": job.page_title,
        "page_summary": job.page_summary,
        "duration": round(job.duration, 2),
        "chunk_target_sec": S.chunk_target_sec,
        "updated_at": int(time.time()),
        "done_chunks": done_list,
        "done_ranges": done_rivers,
        "subs": subs_dict,
        "asr_segs": asr_dict,
        "bible": bible,
    }
    with _cache_io_lock:
        try:
            tmp = path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(path)
        except Exception as e:
            print(f"[Cache] Warning: failed saving {path.name}: {e}", flush=True)


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
    """Select the best media URL."""
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
        video_code: str | None = None,
        page_title: str | None = None,
        page_summary: str | None = None,
        cached_subs: list[dict] | None = None,
        cached_done_chunks: list[int] | None = None,
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
        self.video_code = _extract_or_sanitize_code(video_code, page_title, referer)
        self.page_title = (page_title or "").strip()
        self.page_summary = (page_summary or "").strip()
        self.client_cached_subs = cached_subs or []
        self.client_cached_done_chunks = cached_done_chunks or []
        self.session: VideoSession | None = None
        self.cancel = threading.Event()
        self.lock = threading.Lock()

    def next_batch(self) -> list[int]:
        """Two-Tier Adaptive Batch Selector:
        - Tier 1 (Instant 15s Playhead Chunk): If the chunk at `self.pointer` (current playback / seek position)
          is NOT yet done, return ONLY `[self.pointer]` so the first subtitle appears on screen in ~1.5s!
        - Tier 2 (150s Mega-Batch): Once `self.pointer` is done, group up to `S.mega_batch_sec` (150s = 10 chunks)
          of consecutive undone chunks into a single Mega-Batch!"""
        todo = [c.index for c in self.chunks if c.index not in self.done_chunks]
        if not todo:
            return []

        # Tier 1: Playhead chunk is not ready yet -> process single 15s chunk immediately!
        if self.pointer in todo:
            return [self.pointer]

        # Tier 2: Playhead chunk is already ready -> build a 150s Mega-Batch of consecutive undone chunks
        first = next((i for i in todo if i >= self.pointer), todo[0])
        batch = [first]
        acc_dur = self.chunks[first].end - self.chunks[first].start
        cur = first + 1
        while cur < len(self.chunks) and cur not in self.done_chunks:
            c_dur = self.chunks[cur].end - self.chunks[cur].start
            if acc_dur + c_dur > S.mega_batch_sec + 1.0:
                break
            batch.append(cur)
            acc_dur += c_dur
            cur += 1
        return batch


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
    """Create clean 15s base chunks so start/seek is 15s while Mega-Batches group 10 chunks (150s) at once."""
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


def _restore_cache_into_job(job: Job) -> tuple[dict[int, list[Seg]], dict]:
    """Restore saved subtitles and Whisper ASR segments by timestamp from disk cache (`workspace/cache/<CODE>.json`)
    and browser `localStorage` cache—even if chunk duration changed from 10s to 15s!"""
    cached_segs: dict[int, list[Seg]] = {}
    saved_bible: dict = {}

    disk_data = _load_video_cache(job.video_code) if job.video_code else {}
    same_grid = abs(float(disk_data.get("chunk_target_sec", S.chunk_target_sec)) - S.chunk_target_sec) < 0.5

    if disk_data:
        if not job.page_title and disk_data.get("page_title"):
            job.page_title = disk_data["page_title"]
        if not job.page_summary and disk_data.get("page_summary"):
            job.page_summary = disk_data["page_summary"]
        if isinstance(disk_data.get("bible"), dict):
            saved_bible = disk_data["bible"]

        # Map all saved ASR segments to the current chunk grid by timestamp midpoint
        raw_asr = disk_data.get("asr_segs") or {}
        for _, seg_list in raw_asr.items():
            for g in seg_list:
                try:
                    start = float(g["start"])
                    end = float(g["end"])
                    mid = (start + end) / 2.0
                    target = next((c.index for c in job.chunks if c.start <= mid < c.end), None)
                    if target is not None:
                        lst = cached_segs.setdefault(target, [])
                        if not any(abs(x.start - start) < 0.08 for x in lst):
                            lst.append(
                                Seg(
                                    start=start,
                                    end=end,
                                    zh=str(g.get("zh", "")),
                                    vi=str(g.get("vi", "")),
                                    gender=str(g.get("gender", "unknown")),
                                    f0=int(g.get("f0", 0)),
                                )
                            )
                except Exception:
                    pass

        # Map all saved Vietnamese subtitles to the current chunk grid by timestamp midpoint
        raw_subs = disk_data.get("subs") or {}
        for _, sub_list in raw_subs.items():
            for s in sub_list:
                try:
                    start = float(s["start"])
                    end = float(s["end"])
                    vi = str(s.get("vi", "")).strip()
                    zh = str(s.get("zh", vi)).strip()
                    gender = str(s.get("gender", "unknown"))
                    if not vi:
                        continue
                    mid = (start + end) / 2.0
                    target = next((c.index for c in job.chunks if c.start <= mid < c.end), None)
                    if target is not None:
                        sub_arr = job.subs.setdefault(target, [])
                        if not any(abs(float(x.get("start", -99)) - start) < 0.08 for x in sub_arr):
                            sub_arr.append(
                                {"start": round(start, 2), "end": round(end, 2), "zh": zh, "vi": vi, "gender": gender}
                            )
                        seg_arr = cached_segs.setdefault(target, [])
                        if not any(abs(x.start - start) < 0.08 for x in seg_arr):
                            seg_arr.append(Seg(start=start, end=end, zh=zh, vi=vi, gender=gender))
                        job.done_chunks.add(target)
                except Exception:
                    pass

        if same_grid:
            for c_idx in disk_data.get("done_chunks") or []:
                if isinstance(c_idx, int) and 0 <= c_idx < len(job.chunks):
                    job.done_chunks.add(c_idx)
                    job.subs.setdefault(c_idx, [])
                    cached_segs.setdefault(c_idx, [])
        elif disk_data.get("done_ranges"):
            for r in disk_data["done_ranges"]:
                r0, r1 = float(r.get("start", 0)), float(r.get("end", 0))
                for c in job.chunks:
                    if c.start >= r0 - 0.5 and c.end <= r1 + 0.5:
                        job.done_chunks.add(c.index)
                        job.subs.setdefault(c.index, [])
                        cached_segs.setdefault(c.index, [])

    # Merge any browser localStorage cached subtitles sent by the client
    if job.client_cached_subs:
        for s in job.client_cached_subs:
            try:
                start = float(s.get("start", 0.0))
                end = float(s.get("end", start + 1.5))
                vi = str(s.get("vi", "")).strip()
                zh = str(s.get("zh", vi)).strip()
                gender = str(s.get("gender", "unknown"))
                if not vi:
                    continue
                mid = (start + end) / 2.0
                target = next((c.index for c in job.chunks if c.start <= mid < c.end), None)
                if target is not None:
                    existing = job.subs.setdefault(target, [])
                    if not any(abs(float(x.get("start", -99)) - start) < 0.08 for x in existing):
                        existing.append(
                            {"start": round(start, 2), "end": round(end, 2), "zh": zh, "vi": vi, "gender": gender}
                        )
                    seg_list = cached_segs.setdefault(target, [])
                    if not any(abs(g.start - start) < 0.08 for g in seg_list):
                        seg_list.append(Seg(start=start, end=end, zh=zh, vi=vi, gender=gender))
                    job.done_chunks.add(target)
            except Exception:
                pass

    total_restored_subs = sum(len(v) for v in job.subs.values())
    if job.done_chunks or cached_segs:
        print(
            f"[Cache] Video '{job.video_code}': Restored {len(job.done_chunks)}/{len(job.chunks)} finished chunks "
            f"({total_restored_subs} Vietnamese subtitles) + {len(cached_segs)} Whisper ASR chunks from cache! "
            f"Skipping Whisper for cached chunks.",
            flush=True,
        )
    return cached_segs, saved_bible


def run_job(job: Job) -> None:
    try:
        if job.cancel.is_set():
            job.state = "cancelled"
            return

        if job.video_code or job.page_title or job.page_summary:
            print("------------------------------------------------------------", flush=True)
            if job.video_code:
                print(f"[Context] Video Code : {job.video_code}", flush=True)
            if job.page_title:
                print(f"[Context] Page Title : {job.page_title[:120]}", flush=True)
            if job.page_summary:
                print(f"[Context] Plot Summary ({len(job.page_summary)} chars): {job.page_summary[:220]}...", flush=True)
            print("------------------------------------------------------------", flush=True)

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

        cached_segs, saved_bible = _restore_cache_into_job(job)

        if len(job.done_chunks) >= len(job.chunks) and len(job.chunks) > 0:
            job.state = "done"
            print(
                f"[Cache] All {len(job.chunks)}/{len(job.chunks)} chunks for '{job.video_code}' are already translated in cache! Ready immediately.",
                flush=True,
            )
            return

        if job.cancel.is_set():
            job.state = "cancelled"
            return

        with gpu_lock:
            if job.cancel.is_set():
                job.state = "cancelled"
                return
            eng = _engine()
            tr = Translator(
                S,
                video_code=job.video_code,
                page_title=job.page_title,
                page_summary=job.page_summary,
                initial_bible=saved_bible,
            )

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
            cached_segs=cached_segs,
            on_asr_chunk=lambda _idx, _slist: _save_video_cache(job),
        )
        job.session = session
        job.state = "running"

        for prev_i in sorted(job.subs):
            if prev_i < job.pointer:
                for sub_item in job.subs[prev_i]:
                    spk = "Nam" if sub_item.get("gender") == "male" else "Nữ"
                    tr.history.append((sub_item.get("zh", ""), sub_item.get("vi", ""), spk))

        while not job.cancel.is_set():
            batch = job.next_batch()
            if not batch or job.cancel.is_set():
                break

            b_start = job.chunks[batch[0]].start
            b_end = job.chunks[batch[-1]].end
            b_dur = max(0.1, b_end - b_start)

            if len(batch) == 1:
                print(
                    f"\n[FAST-START {b_dur:.0f}s] Processing immediate playhead chunk {batch[0]}/{len(job.chunks)} "
                    f"({b_start:.1f}s -> {b_end:.1f}s)...",
                    flush=True,
                )
            else:
                print(
                    f"\n[MEGA-BATCH {b_dur:.0f}s] Processing {len(batch)} chunks ({batch[0]}..{batch[-1]}/{len(job.chunks)}, "
                    f"{b_start:.1f}s -> {b_end:.1f}s) in 1 contiguous pass...",
                    flush=True,
                )

            with gpu_lock:
                if job.cancel.is_set():
                    break
                subs_by_chunk, timing = session.process_batch(batch, cancel_event=job.cancel)

            if job.cancel.is_set():
                break

            total_new_subs = 0
            with job.lock:
                for idx in batch:
                    job.done_chunks.add(idx)
                    c_subs = subs_by_chunk.get(idx, [])
                    job.subs[idx] = c_subs
                    job.timing[idx] = timing
                    total_new_subs += len(c_subs)

            _save_video_cache(job)
            mode_tag = "FAST-START" if len(batch) == 1 else "MEGA-BATCH"
            print(
                f"[job {job.id}] DONE {mode_tag} ({b_start:.1f}s-{b_end:.1f}s, {b_dur:.0f}s audio) -> "
                f"{total_new_subs} Vietnamese sub(s) ready ({len(job.done_chunks)}/{len(job.chunks)} chunks complete, "
                f"saved to cache '{job.video_code or 'session'}') | timing={timing} RTF={timing.get('total', 0.0) / b_dur:.2f}\n"
                f"------------------------------------------------------------",
                flush=True,
            )

        session.cancel.set()
        job.state = "cancelled" if job.cancel.is_set() else "done"
        _save_video_cache(job)
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
    video_code: str | None = None
    page_title: str | None = None
    page_summary: str | None = None
    cached_subs: list[dict] | None = None
    cached_done_chunks: list[int] | None = None


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
        video_code=req.video_code,
        page_title=req.page_title,
        page_summary=req.page_summary,
        cached_subs=req.cached_subs,
        cached_done_chunks=req.cached_done_chunks,
    )
    jobs[job.id] = current = job
    threading.Thread(target=run_job, args=(job,), daemon=True).start()
    return {"job_id": job.id, "video_code": job.video_code}


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
    _save_video_cache(j)
    print(f"[server] Job {job_id} cancelled by client stop request (progress saved to cache)", flush=True)
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
            _save_video_cache(j)
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
            "video_code": j.video_code,
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
    print(f"[Config] Chunking Mode     : {S.chunk_target_sec:.0f}s First Chunk -> {S.mega_batch_sec:.0f}s Mega-Batches (up to {S.max_lines_per_batch} lines/call)", flush=True)
    print(f"[Config] Translation API   : {S.base_url}", flush=True)
    print(f"[Config] Translation Model : {S.model}", flush=True)
    print(f"[Config] OpenRouter Key    : {masked_key}", flush=True)
    print(f"[Config] Subtitle Cache    : {CACHE_DIR}", flush=True)
    print("============================================================", flush=True)
    _engine()  # load Whisper model up front so the first request is fast
    print("[server] Whisper ASR ready (Subtitle-Only mode)", flush=True)
    uvicorn.run(app, host="127.0.0.1", port=8765, log_level="warning")


if __name__ == "__main__":
    main()
