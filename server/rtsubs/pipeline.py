"""Subtitle-only chunk & Mega-Batch processing + resident Whisper ASR engine + Python HLS segment streamer."""
from __future__ import annotations

import concurrent.futures
import re
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import numpy as np

from .asr import Asr, Seg
from .chunker import Chunk
from .config import Settings
from .translate import Translator

HLS_INPUT_ARGS = [
    "-allowed_extensions",
    "ALL",
    "-protocol_whitelist",
    "file,http,https,tcp,tls,crypto,data",
]


def _extra_demux_args(path: str | Path) -> list[str]:
    s = str(path).lower()
    if ".m3u8" in s or "mpegurl" in s:
        return HLS_INPUT_ARGS
    return []


def _strip_fake_image_header(data: bytes) -> bytes:
    """Some HLS CDNs (like surrit.com) name MPEG-TS segments .jpeg/.png or prepend a fake image header.
    If the segment is MPEG-TS (0x47 sync byte every 188 bytes), strip any leading fake header bytes."""
    if not data or data[0] == 0x47:
        return data
    if data[:4] in (b"ID3\x03", b"ID3\x04", b"OggS", b"RIFF") or data[4:8] in (b"ftyp", b"moof", b"styp"):
        return data
    limit = min(len(data) - 377, 8192)
    for pos in range(max(0, limit)):
        if data[pos] == 0x47 and data[pos + 188] == 0x47 and data[pos + 376] == 0x47:
            return data[pos:]
    return data


@dataclass
class HlsPlaylist:
    """Parsed HLS media playlist that downloads .ts/.jpeg/.png segments in Python and pipes raw bytes
    to FFmpeg via stdin (pipe:0), completely bypassing FFmpeg 7.1's hls.c allowed_segment_extensions block."""

    media_url: str
    headers: dict[str, str]
    segments: list[tuple[float, float, str]] = field(default_factory=list)  # (start_sec, end_sec, url)
    duration: float = 0.0
    init_map_url: str | None = None
    key_line: str | None = None
    _init_bytes: bytes | None = None

    @classmethod
    def from_url(cls, url: str, headers: dict[str, str]) -> HlsPlaylist | None:
        try:
            text, final_url = cls._fetch_text(url, headers)
            if "#EXTM3U" not in text:
                return None

            if "#EXT-X-STREAM-INF" in text and "#EXTINF:" not in text:
                variant_url = cls._pick_variant_url(text, final_url)
                if not variant_url:
                    return None
                print(f"[HLS] Resolved master playlist -> {variant_url[:100]}", flush=True)
                text, final_url = cls._fetch_text(variant_url, headers)
                if "#EXTM3U" not in text or "#EXTINF:" not in text:
                    return None

            segments: list[tuple[float, float, str]] = []
            cur_time = 0.0
            pending_dur: float | None = None
            init_map_url: str | None = None
            key_line: str | None = None

            for raw_line in text.splitlines():
                line = raw_line.strip()
                if not line:
                    continue
                if line.startswith("#EXT-X-MAP:"):
                    m = re.search(r'URI="([^"]+)"', line)
                    if m:
                        init_map_url = urllib.parse.urljoin(final_url, m.group(1))
                elif line.startswith("#EXT-X-KEY:"):
                    if "METHOD=NONE" in line:
                        key_line = None
                    else:
                        key_line = re.sub(
                            r'URI="([^"]+)"',
                            lambda m: f'URI="{urllib.parse.urljoin(final_url, m.group(1))}"',
                            line,
                        )
                elif line.startswith("#EXTINF:"):
                    val = line[8:].split(",")[0].strip()
                    try:
                        pending_dur = float(val)
                    except ValueError:
                        pending_dur = 5.0
                elif not line.startswith("#"):
                    dur = pending_dur if (pending_dur is not None and pending_dur > 0) else 5.0
                    seg_url = urllib.parse.urljoin(final_url, line)
                    segments.append((cur_time, cur_time + dur, seg_url))
                    cur_time += dur
                    pending_dur = None

            if not segments:
                return None

            return cls(
                media_url=final_url,
                headers=headers,
                segments=segments,
                duration=round(cur_time, 2),
                init_map_url=init_map_url,
                key_line=key_line,
            )
        except Exception as e:
            print(f"[HLS] Failed to parse playlist {url[:90]}: {e}", flush=True)
            return None

    @staticmethod
    def _fetch_text(url: str, headers: dict[str, str]) -> tuple[str, str]:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=10) as resp:
            final_url = resp.geturl() or url
            data = resp.read(2_000_000)
        return data.decode("utf-8", errors="ignore"), final_url

    def _fetch_bytes(self, url: str) -> bytes:
        last_err: Exception | None = None
        for attempt in range(3):
            try:
                req = urllib.request.Request(url, headers=self.headers)
                with urllib.request.urlopen(req, timeout=15) as resp:
                    return resp.read()
            except Exception as e:
                last_err = e
                time.sleep(0.25 * (attempt + 1))
        raise RuntimeError(f"Segment fetch failed after 3 attempts ({url[:80]}): {last_err}")

    @staticmethod
    def _pick_variant_url(master_text: str, base_url: str) -> str | None:
        """Pick a variant stream from a master playlist.
        Prefers lowest/moderate bandwidth (e.g. 360p/480p/720p) so 150s Mega-Batches download in ~1s while keeping identical audio."""
        lines = [ln.strip() for ln in master_text.splitlines() if ln.strip()]
        variants: list[tuple[int, str]] = []
        for idx, line in enumerate(lines):
            if line.startswith("#EXT-X-STREAM-INF"):
                bw = 1_500_000
                m = re.search(r"BANDWIDTH=(\d+)", line)
                if m:
                    bw = int(m.group(1))
                for nxt in lines[idx + 1 :]:
                    if not nxt.startswith("#"):
                        variants.append((bw, urllib.parse.urljoin(base_url, nxt)))
                        break
        if not variants:
            return None
        variants.sort(key=lambda x: x[0])
        return variants[0][1]

    def decode_window(self, start: float, duration: float, sr: int = 16000) -> np.ndarray:
        """Download all HLS segments overlapping [start, start+duration] (15s chunk or 150s Mega-Batch)
        in parallel (up to 8 threads) and decode via FFmpeg stdin."""
        end = start + duration
        hits = [(s0, s1, u) for (s0, s1, u) in self.segments if s1 > start and s0 < end]
        if not hits:
            return np.zeros(0, dtype=np.float32)

        t0 = time.time()
        try:
            if self.init_map_url and self._init_bytes is None:
                self._init_bytes = self._fetch_bytes(self.init_map_url)

            # Use up to 8 concurrent threads so even a 150s Mega-Batch (~25-30 segments) downloads in ~1s
            with concurrent.futures.ThreadPoolExecutor(max_workers=min(8, len(hits))) as pool:
                seg_buffers = list(pool.map(lambda item: self._fetch_bytes(item[2]), hits))
        except Exception as e:
            print(f"[Step 1/3 - Audio ERROR] Failed downloading HLS segments for {start:.1f}s-{end:.1f}s: {e}", flush=True)
            return np.zeros(0, dtype=np.float32)

        total_kb = sum(len(b) for b in seg_buffers) / 1024.0

        if self.key_line:
            pcm = self._decode_encrypted_segments(hits, seg_buffers, sr)
        else:
            cleaned = [_strip_fake_image_header(b) for b in seg_buffers]
            if self._init_bytes:
                cleaned.insert(0, self._init_bytes)
            raw_stream = b"".join(cleaned)
            cmd = [
                "ffmpeg",
                "-v",
                "error",
                "-i",
                "pipe:0",
                "-vn",
                "-ac",
                "1",
                "-ar",
                str(sr),
                "-f",
                "f32le",
                "-",
            ]
            proc = subprocess.run(cmd, input=raw_stream, capture_output=True)
            if proc.returncode != 0 and len(proc.stdout) == 0:
                err_msg = proc.stderr.decode("utf-8", errors="replace").strip()
                print(f"[Step 1/3 - Audio WARNING] FFmpeg pipe decode on {start:.1f}s: {err_msg[:220]}", flush=True)
                return np.zeros(0, dtype=np.float32)
            pcm = np.frombuffer(proc.stdout, dtype=np.float32).copy()

        first_seg_start = hits[0][0]
        skip_sec = max(0.0, start - first_seg_start)
        skip_samples = int(skip_sec * sr)
        take_samples = int(duration * sr)
        if skip_samples < len(pcm):
            pcm = pcm[skip_samples : skip_samples + take_samples]

        dec_dur = len(pcm) / sr if len(pcm) > 0 else 0.0
        elapsed = time.time() - t0
        print(
            f"[Step 1/3 - Audio] Window {start:.1f}s -> {end:.1f}s: "
            f"Downloaded {len(hits)} HLS segment(s) ({total_kb:.0f} KB) in {elapsed:.2f}s -> Decoded {dec_dur:.1f}s audio",
            flush=True,
        )
        return pcm

    def _decode_encrypted_segments(
        self, hits: list[tuple[float, float, str]], seg_buffers: list[bytes], sr: int
    ) -> np.ndarray:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            m3u8_lines = [
                "#EXTM3U",
                "#EXT-X-VERSION:3",
                "#EXT-X-TARGETDURATION:15",
                self.key_line or "",
            ]
            for idx, ((s0, s1, _), buf) in enumerate(zip(hits, seg_buffers)):
                seg_file = tmp / f"seg_{idx}.ts"
                seg_file.write_bytes(buf)
                m3u8_lines.append(f"#EXTINF:{max(0.5, s1 - s0):.3f},")
                m3u8_lines.append(seg_file.as_posix())
            m3u8_lines.append("#EXT-X-ENDLIST")
            local_m3u8 = tmp / "window.m3u8"
            local_m3u8.write_text("\n".join(m3u8_lines), encoding="utf-8")

            hdr_list = [f"{k}: {v}" for k, v in self.headers.items()]
            cmd = [
                "ffmpeg",
                "-v",
                "error",
                "-headers",
                "\r\n".join(hdr_list) + "\r\n",
                *HLS_INPUT_ARGS,
                "-i",
                str(local_m3u8),
                "-vn",
                "-ac",
                "1",
                "-ar",
                str(sr),
                "-f",
                "f32le",
                "-",
            ]
            proc = subprocess.run(cmd, capture_output=True)
            return np.frombuffer(proc.stdout, dtype=np.float32).copy()


def decode(path: str | Path, sr: int = 16000, ch: int = 1, input_args: list[str] | None = None) -> np.ndarray:
    """Decode audio from a file, direct URL, or .m3u8 playlist into float32 mono 16kHz PCM."""
    print(f"[Step 1/3 - Audio] Decoding full audio stream from: {str(path)[:100]} ...", flush=True)
    cmd = [
        "ffmpeg",
        "-v",
        "error",
        "-reconnect",
        "1",
        "-reconnect_streamed",
        "1",
        "-reconnect_delay_max",
        "5",
        *(input_args or []),
        *_extra_demux_args(path),
        "-i",
        str(path),
        "-vn",
        "-ac",
        str(ch),
        "-ar",
        str(sr),
        "-f",
        "f32le",
        "-",
    ]
    proc = subprocess.run(cmd, capture_output=True)
    if proc.returncode != 0:
        err_msg = proc.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"FFmpeg decode failed: {err_msg[:300]}")
    a = np.frombuffer(proc.stdout, dtype=np.float32)
    dur = len(a) / (sr * ch) if len(a) > 0 else 0.0
    print(f"[Step 1/3 - Audio] Decoded {dur:.1f}s of audio into memory.", flush=True)
    return a.reshape(-1, ch) if ch > 1 else a.copy()


def decode_window(
    path: str | Path, start: float, duration: float, sr: int = 16000, input_args: list[str] | None = None
) -> np.ndarray:
    """Decode a specific [start, start+duration] time slice from a direct MP4/WebM URL via ffmpeg -ss."""
    print(f"[Step 1/3 - Audio] Streaming audio window {start:.1f}s -> {start + duration:.1f}s ...", flush=True)
    cmd = [
        "ffmpeg",
        "-v",
        "error",
        "-reconnect",
        "1",
        "-reconnect_streamed",
        "1",
        "-reconnect_delay_max",
        "5",
        *(input_args or []),
        *_extra_demux_args(path),
    ]
    if start > 0:
        cmd += ["-ss", f"{start:.2f}"]
    cmd += [
        "-i",
        str(path),
    ]
    if duration > 0:
        cmd += ["-t", f"{duration:.2f}"]
    cmd += [
        "-vn",
        "-ac",
        "1",
        "-ar",
        str(sr),
        "-f",
        "f32le",
        "-",
    ]
    proc = subprocess.run(cmd, capture_output=True)
    if proc.returncode != 0:
        err_msg = proc.stderr.decode("utf-8", errors="replace").strip()
        print(f"[Step 1/3 - Audio WARNING] Window {start:.1f}s: {err_msg[:240]}", flush=True)
        return np.zeros(0, dtype=np.float32)
    pcm = np.frombuffer(proc.stdout, dtype=np.float32).copy()
    dec_dur = len(pcm) / sr if len(pcm) > 0 else 0.0
    print(f"[Step 1/3 - Audio] Window {start:.1f}s -> {start + duration:.1f}s: Decoded {dec_dur:.1f}s audio", flush=True)
    return pcm


class Engine:
    """Whisper ASR model loaded once in GPU/CPU memory."""

    def __init__(self, s: Settings):
        self.s = s
        self.asr = Asr(s)
        self.asr_lock = threading.Lock()  # Whisper is shared between active chunk and look-ahead thread

    def close(self) -> None:
        pass


class VideoSession:
    """One video session: supports both 15s instant playhead chunks and 150s contiguous Mega-Batches."""

    def __init__(
        self,
        eng: Engine,
        tr: Translator,
        a16: np.ndarray | None,
        chunks: list[Chunk],
        transcript: list[dict] | None = None,
        transcript_lang: str | None = None,
        stream_url: str | None = None,
        stream_args: list[str] | None = None,
        hls_playlist: HlsPlaylist | None = None,
        cached_segs: dict[int, list[Seg]] | None = None,
        on_asr_chunk: Callable[[int, list[Seg]], None] | None = None,
    ):
        self.eng, self.tr, self.a16, self.chunks = eng, tr, a16, chunks
        self.stream_url = stream_url
        self.stream_args = stream_args or []
        self.hls_playlist = hls_playlist
        self.transcript = transcript
        self.transcript_lang = (transcript_lang or "").lower()
        self._segs: dict[int, list[Seg]] = dict(cached_segs) if cached_segs else {}
        self._asr_time: dict[int, float] = {i: 0.0 for i in self._segs}
        self._bible_built_at_segs = 0
        self.on_asr_chunk = on_asr_chunk
        self.cancel = threading.Event()
        self.play_time = 0.0
        self._ahead: threading.Thread | None = None

        if self.transcript:
            self._init_from_transcript()

    def _init_from_transcript(self) -> None:
        """Map external transcript cues to planned chunks and avoid running Whisper."""
        is_vi = self.transcript_lang.startswith("vi")
        if not is_vi and self.transcript:
            sample = " ".join(c.get("text", "") for c in self.transcript[:10])
            vi_chars = set("àáảãạăằắẳẵặâầấẩẫậèéẻẽẹêềếểễệìíỉĩịòóỏõọôồốổỗộơờớởỡợùúủũụưừứửữựỳýỷỹỵđ")
            if any(ch in vi_chars for ch in sample.lower()):
                is_vi = True

        for c in self.chunks:
            self._segs[c.index] = []
            self._asr_time[c.index] = 0.0

        for cue in self.transcript:
            start = float(cue.get("start", 0.0))
            end = float(cue.get("end", start + 2.0))
            text = cue.get("text", "").strip()
            if not text:
                continue
            mid = (start + end) / 2.0
            target_chunk = None
            for c in self.chunks:
                if c.start <= mid < c.end:
                    target_chunk = c
                    break
            if target_chunk is None and self.chunks:
                target_chunk = min(self.chunks, key=lambda c: abs(c.start - start))
            if target_chunk:
                seg = Seg(start=start, end=end, zh=text, vi=text if is_vi else "")
                self._segs[target_chunk.index].append(seg)

        total_segs = sum(len(v) for v in self._segs.values())
        print(
            f"[transcript] Pre-populated {total_segs} segments across {len(self.chunks)} chunks (is_vietnamese={is_vi})",
            flush=True,
        )

    def _range_audio(self, start_sec: float, end_sec: float) -> np.ndarray:
        """Decode audio for a contiguous time range [start_sec, end_sec] (either 15s or 150s Mega-Batch)."""
        dur = max(0.1, end_sec - start_sec)
        if self.a16 is not None:
            s_idx = int(start_sec * 16000)
            e_idx = int(end_sec * 16000)
            if s_idx < len(self.a16):
                return self.a16[s_idx:e_idx]
        if self.hls_playlist is not None:
            return self.hls_playlist.decode_window(start_sec, dur, 16000)
        if self.stream_url:
            return decode_window(self.stream_url, start_sec, dur, 16000, self.stream_args)
        return np.zeros(0, dtype=np.float32)

    def segs_for(self, i: int) -> list[Seg]:
        self.segs_for_batch([i])
        return self._segs.get(i, [])

    def segs_for_batch(self, batch_indices: list[int]) -> list[Seg]:
        """Ensure all chunk indices in `batch_indices` are transcribed by Whisper.
        Contiguous untranscribed chunks in `batch_indices` are decoded and transcribed in ONE single pass
        so 150s Mega-Batches never cut Japanese sentences at 15s boundaries!"""
        if not batch_indices:
            return []
        with self.eng.asr_lock:
            missing = [idx for idx in batch_indices if idx not in self._segs]
            if missing:
                # Group missing indices into contiguous runs (usually 1 single run like [1..10])
                runs: list[list[int]] = []
                for idx in sorted(missing):
                    if not runs or idx != runs[-1][-1] + 1:
                        runs.append([idx])
                    else:
                        runs[-1].append(idx)

                for run in runs:
                    if self.cancel.is_set():
                        break
                    start_sec = self.chunks[run[0]].start
                    end_sec = self.chunks[run[-1]].end
                    t0 = time.time()
                    pcm = self._range_audio(start_sec, end_sec)
                    for idx in run:
                        self._segs[idx] = []
                    if len(pcm) > 800:
                        raw_segs = self.eng.asr.transcribe(pcm, start_sec)
                        for seg in raw_segs:
                            mid = (seg.start + seg.end) / 2.0
                            assigned = run[-1]
                            for idx in run:
                                c = self.chunks[idx]
                                if c.start <= mid <= c.end:
                                    assigned = idx
                                    break
                            self._segs[assigned].append(seg)
                    else:
                        print(
                            f"[Step 1/3 - Audio WARNING] Window {start_sec:.1f}s -> {end_sec:.1f}s returned empty audio!",
                            flush=True,
                        )
                    elapsed_per = (time.time() - t0) / max(1, len(run))
                    for idx in run:
                        self._asr_time[idx] = elapsed_per

                if self.on_asr_chunk is not None:
                    try:
                        self.on_asr_chunk(batch_indices[-1], [])
                    except Exception:
                        pass

            return [g for idx in batch_indices for g in self._segs.get(idx, [])]

    def _lines(self) -> list[tuple[float, str, str]]:
        return [(g.start, g.zh, g.gender) for i in sorted(self._segs) for g in self._segs[i]]

    def maybe_update_bible(self) -> None:
        done = len(self._segs)
        all_lines = self._lines()
        if not all_lines:
            return
        if (
            (self._bible_built_at_segs == 0 and done >= 1)
            or (done - self._bible_built_at_segs >= self.eng.s.bible_every)
            or (done == len(self.chunks) and self._bible_built_at_segs < done)
        ):
            self._bible_built_at_segs = done
            self.tr.understand(all_lines, complete=done == len(self.chunks))

    def warm_up(self, start_index: int = 0) -> None:
        """Transcribe initial 15s chunk at playhead immediately."""
        if self.cancel.is_set() or not self.chunks:
            return
        if self.transcript:
            is_vi = self.transcript_lang.startswith("vi") or any(
                bool(g.vi) for segs in self._segs.values() for g in segs
            )
            if not is_vi:
                self.tr.understand(self._lines(), complete=True)
            return

        first_idx = max(0, min(start_index, len(self.chunks) - 1))
        self.play_time = self.chunks[first_idx].start
        self.segs_for(first_idx)

    def process_batch(
        self, batch_indices: list[int], cancel_event: threading.Event | None = None
    ) -> tuple[dict[int, list[dict]], dict]:
        """Process either a single 15s chunk (`len(batch_indices) == 1`) or a 150s Mega-Batch (`len > 1`).
        Returns (subs_by_chunk_index, timing_dict)."""
        if not batch_indices:
            return {}, {}
        self.play_time = self.chunks[batch_indices[0]].start
        t_start = time.time()
        if cancel_event and cancel_event.is_set():
            return {}, {}

        all_segs = self.segs_for_batch(batch_indices)
        t_asr = sum(self._asr_time.get(idx, 0.0) for idx in batch_indices)

        # Build / refresh Story Bible if we have enough transcribed context
        if len(batch_indices) > 1 or self._bible_built_at_segs == 0:
            self.maybe_update_bible()

        if cancel_event and cancel_event.is_set():
            return {}, {}

        batch_end = self.chunks[batch_indices[-1]].end
        slots = [max(1.0, min(g.end, batch_end) - g.start) for g in all_segs]
        t_tr_start = time.time()
        if not all_segs or all(bool(g.vi) for g in all_segs):
            t_tr = 0.0
        else:
            self.tr.translate(all_segs, slots)
            t_tr = time.time() - t_tr_start
            for g in all_segs:
                if g.vi.strip():
                    g_tag = "Nam" if g.gender == "male" else "Nữ"
                    print(f"   [VI {g.start:6.1f}s -> {min(g.end, batch_end):6.1f}s] [{g_tag}] {g.vi}", flush=True)

        subs_by_chunk: dict[int, list[dict]] = {}
        for idx in batch_indices:
            c = self.chunks[idx]
            subs_by_chunk[idx] = [
                {
                    "start": round(g.start, 2),
                    "end": round(min(g.end, batch_end), 2),
                    "zh": g.zh,
                    "vi": g.vi,
                    "gender": g.gender,
                }
                for g in self._segs.get(idx, [])
                if g.vi.strip()
            ]

        timing = {
            "asr": round(t_asr, 2),
            "translate": round(t_tr, 2),
            "total": round(time.time() - t_start + t_asr, 2),
        }
        return subs_by_chunk, timing

    def process(self, i: int, cancel_event: threading.Event | None = None) -> tuple[list[dict], dict]:
        subs_map, timing = self.process_batch([i], cancel_event=cancel_event)
        return subs_map.get(i, []), timing
