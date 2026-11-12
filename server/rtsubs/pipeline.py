"""Subtitle-only chunk processing + resident Whisper ASR engine."""
from __future__ import annotations

import subprocess
import sys
import threading
import time
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import numpy as np

from .asr import Asr, Seg
from .chunker import Chunk
from .config import Settings
from .translate import Translator


def decode(path: str | Path, sr: int = 16000, ch: int = 1, input_args: list[str] | None = None) -> np.ndarray:
    """Decode audio from a file, direct URL, or .m3u8 playlist into float32 mono 16kHz PCM."""
    raw = subprocess.run(
        [
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
        ],
        capture_output=True,
        check=True,
    ).stdout
    a = np.frombuffer(raw, dtype=np.float32)
    return a.reshape(-1, ch) if ch > 1 else a.copy()


def decode_window(
    path: str | Path, start: float, duration: float, sr: int = 16000, input_args: list[str] | None = None
) -> np.ndarray:
    """Decode a specific [start, start+duration] time slice from a URL / .m3u8 playlist quickly via ffmpeg -ss."""
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
    raw = subprocess.run(cmd, capture_output=True, check=True).stdout
    return np.frombuffer(raw, dtype=np.float32).copy()


class Engine:
    """Whisper ASR model loaded once in GPU/CPU memory."""

    def __init__(self, s: Settings):
        self.s = s
        self.asr = Asr(s)
        self.asr_lock = threading.Lock()  # Whisper is shared between active chunk and look-ahead thread

    def close(self) -> None:
        pass


class VideoSession:
    """One video session: transcribes chunks ahead of playback and translates into Vietnamese subtitles."""

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
    ):
        self.eng, self.tr, self.a16, self.chunks = eng, tr, a16, chunks
        self.stream_url = stream_url
        self.stream_args = stream_args or []
        self.transcript = transcript
        self.transcript_lang = (transcript_lang or "").lower()
        self._segs: dict[int, list[Seg]] = {}
        self._asr_time: dict[int, float] = {}
        self.cancel = threading.Event()
        self.play_time = 0.0  # start of the chunk being processed; read-ahead stays within a window of it
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

    def _chunk_audio(self, c: Chunk) -> np.ndarray:
        if self.a16 is not None:
            s_idx = int(c.start * 16000)
            e_idx = int(c.end * 16000)
            if s_idx < len(self.a16):
                return self.a16[s_idx:e_idx]
        if self.stream_url:
            return decode_window(self.stream_url, c.start, c.end - c.start, 16000, self.stream_args)
        return np.zeros(0, dtype=np.float32)

    def segs_for(self, i: int) -> list[Seg]:
        with self.eng.asr_lock:
            if i not in self._segs:
                c = self.chunks[i]
                t = time.time()
                pcm = self._chunk_audio(c)
                if len(pcm) > 800:
                    self._segs[i] = self.eng.asr.transcribe(pcm, c.start)
                else:
                    self._segs[i] = []
                self._asr_time[i] = time.time() - t
            return self._segs[i]

    def _lines(self) -> list[tuple[float, str]]:
        return [(g.start, g.zh) for i in sorted(self._segs) for g in self._segs[i]]

    def warm_up(self, start_index: int = 0) -> None:
        """Transcribe initial chunk at playhead, build initial context, then read ahead in background."""
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
        if self.cancel.is_set():
            return
        if len(self.chunks) > 1 and not self.cancel.is_set():
            self._ahead = threading.Thread(target=self._read_ahead, args=(first_idx,), daemon=True)
            self._ahead.start()

    def _read_ahead(self, start_idx: int = 0) -> None:
        s = self.eng.s
        last_bible = 0
        order = list(range(start_idx, len(self.chunks))) + list(range(0, start_idx))
        for idx in order:
            c = self.chunks[idx]
            while not self.cancel.is_set() and c.start > self.play_time + s.story_lookahead_sec:
                if self.cancel.wait(0.3):
                    return
            if self.cancel.is_set():
                return
            self.segs_for(c.index)
            if self.cancel.is_set():
                return
            done = len(self._segs)
            if (done == 2 and last_bible == 0) or (done - last_bible >= s.bible_every) or (done == len(self.chunks)):
                last_bible = done
                self.tr.understand(self._lines(), complete=done == len(self.chunks))

    def process(self, i: int, cancel_event: threading.Event | None = None) -> tuple[list[dict], dict]:
        self.play_time = self.chunks[i].start
        return process_chunk(
            self.tr,
            self.chunks[i],
            self.segs_for(i),
            self._asr_time.get(i, 0.0),
            cancel_event=cancel_event,
        )


def process_chunk(
    tr: Translator,
    c: Chunk,
    segs: list[Seg],
    t_asr: float = 0.0,
    cancel_event: threading.Event | None = None,
) -> tuple[list[dict], dict]:
    """Returns (subtitle dicts for the chunk, timing dict). No TTS or audio mixing."""
    t_start = time.time()
    if cancel_event and cancel_event.is_set():
        return [], {}

    slots = [max(1.0, min(g.end, c.end) - g.start) for g in segs]
    t = time.time()
    if not segs or all(bool(g.vi) for g in segs):
        t_tr = 0.0
    else:
        tr.translate(segs, slots)
        t_tr = time.time() - t

    subs = [
        {"start": round(g.start, 2), "end": round(min(g.end, c.end), 2), "zh": g.zh, "vi": g.vi}
        for g in segs
        if g.vi.strip()
    ]
    timing = {
        "asr": round(t_asr, 2),
        "translate": round(t_tr, 2),
        "total": round(time.time() - t_start + t_asr, 2),
    }
    return subs, timing
