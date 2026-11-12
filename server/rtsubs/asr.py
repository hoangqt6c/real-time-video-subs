from __future__ import annotations

import os
from dataclasses import dataclass

os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")

import numpy as np

from .config import Settings

# Prompt hint for Japanese conversational / intimate / 18+ dialogue so Whisper captures soft whispers & sighs accurately
JA_INITIAL_PROMPT = (
    "あ、んっ、だめ、気持ちいい、すごい、そこ、いく、イっちゃう、もっと、好き、いや、待って、"
    "はぁ、ねぇ、ほんとに、どうしよう、恥ずかしい、お願い。"
)


@dataclass
class Seg:
    start: float  # absolute seconds in the source
    end: float
    zh: str  # source language text (Japanese / original)
    vi: str = ""


class Asr:
    def __init__(self, s: Settings):
        import torch  # noqa: F401  (loads cuBLAS DLLs before ctranslate2)
        from faster_whisper import WhisperModel

        self.s = s
        device = "cuda" if torch.cuda.is_available() else "cpu"
        compute = s.whisper_compute if device == "cuda" else "int8"
        print(
            f"[Whisper] Loading model '{s.whisper_model}' on {device.upper()} ({compute})... "
            f"(First run downloads ~1.6GB from HuggingFace, please wait)",
            flush=True,
        )
        self.model = WhisperModel(s.whisper_model, device=device, compute_type=compute)
        print(f"[Whisper] Model '{s.whisper_model}' loaded and ready on {device.upper()}!", flush=True)

    def transcribe(self, audio16k: np.ndarray, offset: float) -> list[Seg]:
        lang = (self.s.source_language or "").strip().lower()
        if lang in ("", "auto", "none"):
            lang = None
        prompt = JA_INITIAL_PROMPT if lang == "ja" else None
        dur = len(audio16k) / 16000.0
        peak = float(np.max(np.abs(audio16k))) if len(audio16k) > 0 else 0.0
        print(
            f"[Step 2/3 - Whisper JA] Transcribing {offset:.1f}s -> {offset + dur:.1f}s "
            f"(lang={lang or 'auto'}, audio_dur={dur:.1f}s, peak_vol={peak:.2f})...",
            flush=True,
        )
        segs, _ = self.model.transcribe(
            audio16k,
            language=lang,
            beam_size=2,
            vad_filter=True,
            vad_parameters=dict(
                threshold=0.32,
                min_speech_duration_ms=120,
                min_silence_duration_ms=250,
                speech_pad_ms=300,
            ),
            condition_on_previous_text=False,
            no_speech_threshold=0.85,
            initial_prompt=prompt,
        )
        out = []
        for g in segs:
            text = g.text.strip()
            if text and g.no_speech_prob < 0.90:
                seg = Seg(offset + g.start, offset + g.end, text)
                out.append(seg)
                print(f"   [JA {seg.start:6.1f}s -> {seg.end:6.1f}s] {seg.zh}", flush=True)
        if not out:
            print(
                f"   [Step 2/3 - Whisper JA] No speech detected in {offset:.1f}s -> {offset + dur:.1f}s "
                f"(silence/music only -> skipping OpenRouter translation)",
                flush=True,
            )
        else:
            print(
                f"   [Step 2/3 - Whisper JA] Found {len(out)} Japanese speech line(s) in {offset:.1f}s -> {offset + dur:.1f}s",
                flush=True,
            )
        return out
