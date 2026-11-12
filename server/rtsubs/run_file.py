"""Chunked subtitle generator on a local video file.
Usage: python -m rtsubs.run_file <video> [--max-sec N]
Outputs: workspace/<video_stem>/subs.json and workspace/<video_stem>/subs.srt
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from .chunker import plan_chunks, speech_timestamps
from .config import Settings
from .pipeline import Engine, VideoSession, decode
from .translate import Translator


def _format_srt_time(seconds: float) -> str:
    ms = int(round((seconds % 1) * 1000))
    total_s = int(seconds)
    s = total_s % 60
    m = (total_s // 60) % 60
    h = total_s // 3600
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _write_srt(subs: list[dict], path: Path) -> None:
    blocks = []
    for idx, sub in enumerate(subs, start=1):
        start = _format_srt_time(float(sub["start"]))
        end = _format_srt_time(float(sub["end"]))
        blocks.append(f"{idx}\n{start} --> {end}\n{sub['vi']}\n")
    path.write_text("\n".join(blocks), encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("video", type=Path)
    ap.add_argument("--max-sec", type=float, default=0)
    args = ap.parse_args()

    s = Settings()
    out_dir = s.workspace / args.video.stem
    out_dir.mkdir(parents=True, exist_ok=True)
    a16 = decode(args.video, 16000, 1)
    duration = min(len(a16) / 16000, args.max_sec or 1e9)
    chunks = plan_chunks(duration, [t for t in speech_timestamps(a16) if t[0] < duration], s)

    eng, tr = Engine(s), Translator(s)
    session = VideoSession(eng, tr, a16, chunks)
    t0 = time.time()
    session.warm_up()
    print(f"[warm-up] chunk 0 transcribed in {time.time() - t0:.1f}s", flush=True)
    subs, total = [], 0.0
    for c in chunks:
        cs, tm = session.process(c.index)
        subs += cs
        total += tm["total"]
        dur = max(0.1, c.end - c.start)
        print(f"[chunk {c.index:02d}] {dur:5.1f}s {tm} RTF={tm['total'] / dur:.2f}", flush=True)

    json_path = out_dir / "subs.json"
    srt_path = out_dir / "subs.srt"
    json_path.write_text(json.dumps(subs, ensure_ascii=False, indent=1), encoding="utf-8")
    _write_srt(subs, srt_path)
    print(f"[done] {duration:.0f}s audio translated in {total:.1f}s (RTF={total / duration:.2f}) -> {srt_path}")
    eng.close()


if __name__ == "__main__":
    main()
