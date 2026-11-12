from __future__ import annotations

from dataclasses import dataclass

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
        self.model = WhisperModel(s.whisper_model, device=device, compute_type=compute)

    def transcribe(self, audio16k: np.ndarray, offset: float) -> list[Seg]:
        lang = (self.s.source_language or "").strip().lower()
        if lang in ("", "auto", "none"):
            lang = None
        prompt = JA_INITIAL_PROMPT if lang == "ja" else None
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
                out.append(Seg(offset + g.start, offset + g.end, text))
        return out
