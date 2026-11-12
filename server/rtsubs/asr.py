from __future__ import annotations

import os
import re
from dataclasses import dataclass

os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")

import numpy as np

from .config import Settings

# Prompt hint for Japanese conversational / intimate / 18+ dialogue so Whisper captures soft whispers & sighs accurately
JA_INITIAL_PROMPT = (
    "あ、んっ、だめ、気持ちいい、すごい、そこ、いく、イっちゃう、もっと、好き、いや、待って、"
    "はぁ、ねぇ、ほんとに、どうしよう、恥ずかしい、お願い。"
)

_MALE_JA_RE = re.compile(r"(俺|僕|お前|おい|こら|だぞ|ぜ|なよ|やるよ)")
_FEMALE_JA_RE = re.compile(r"(あたし|わたし|私|イヤ|いやん|だめぇ|いくぅ|イっちゃう|お願い|あなた)")


@dataclass
class Seg:
    start: float  # absolute seconds in the source
    end: float
    zh: str  # source language text (Japanese / original)
    vi: str = ""
    gender: str = "unknown"  # "male" | "female" | "unknown"
    f0: int = 0  # median fundamental frequency in Hz (0 if unvoiced/whisper)


def detect_voice_gender(segment_pcm: np.ndarray, text: str = "", sr: int = 16000) -> tuple[str, int]:
    """Detect whether a spoken segment is Male ('male') or Female ('female') using fundamental frequency (F0)
    autocorrelation across voiced frames + Japanese gendered lexical cues.
    - Typical Male F0   : 80 Hz - 165 Hz
    - Typical Female F0 : 175 Hz - 420 Hz
    """
    if len(segment_pcm) < int(sr * 0.08):
        if _MALE_JA_RE.search(text):
            return "male", 0
        return "female", 0

    frame_len = 512  # 32ms at 16kHz
    hop_len = 256  # 16ms hop
    lag_min = int(sr / 420)  # ~38 samples (420 Hz max female pitch)
    lag_max = int(sr / 75)   # ~213 samples (75 Hz min male pitch)

    rms_all = float(np.sqrt(np.mean(segment_pcm**2)) + 1e-8)
    energy_thresh = max(0.008, rms_all * 0.35)

    window = np.hanning(frame_len).astype(np.float32)
    f0_estimates: list[float] = []

    for start_idx in range(0, len(segment_pcm) - frame_len, hop_len):
        frame = segment_pcm[start_idx : start_idx + frame_len]
        rms = float(np.sqrt(np.mean(frame**2)))
        if rms < energy_thresh:
            continue
        # Skip unvoiced fricative frames (high zero-crossing rate like 's', 'sh', 'h')
        zcr = float(np.mean(np.abs(np.diff(np.signbit(frame)))))
        if zcr > 0.30:
            continue

        w_frame = (frame - np.mean(frame)) * window
        spec = np.fft.rfft(w_frame, n=1024)
        acf = np.fft.irfft(spec * np.conj(spec), n=1024)[:frame_len]
        if acf[0] <= 1e-8:
            continue
        acf = acf / acf[0]

        search_slice = acf[lag_min:lag_max]
        if len(search_slice) == 0:
            continue
        best_offset = int(np.argmax(search_slice))
        peak_val = float(search_slice[best_offset])
        if peak_val >= 0.28:
            lag = lag_min + best_offset
            f0_estimates.append(sr / float(lag))

    if not f0_estimates:
        if _MALE_JA_RE.search(text) and not _FEMALE_JA_RE.search(text):
            return "male", 0
        return "female", 0

    # Use 60th percentile F0 to capture natural pitch inflection in speech/moans
    f0_med = int(round(float(np.percentile(f0_estimates, 60))))

    # Combine lexical hint near boundary (155Hz - 180Hz)
    if 155 <= f0_med <= 180:
        if _MALE_JA_RE.search(text) and not _FEMALE_JA_RE.search(text):
            return "male", f0_med
        if _FEMALE_JA_RE.search(text) and not _MALE_JA_RE.search(text):
            return "female", f0_med

    gender = "male" if f0_med < 168 else "female"
    return gender, f0_med


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
                s_sample = max(0, int(g.start * 16000))
                e_sample = min(len(audio16k), int(g.end * 16000))
                seg_pcm = audio16k[s_sample:e_sample]
                gender, f0 = detect_voice_gender(seg_pcm, text, 16000)
                seg = Seg(offset + g.start, offset + g.end, text, gender=gender, f0=f0)
                out.append(seg)
                g_label = "Giọng Nam" if gender == "male" else "Giọng Nữ"
                f0_str = f" {f0}Hz" if f0 > 0 else ""
                print(f"   [JA {seg.start:6.1f}s -> {seg.end:6.1f}s] [{g_label}{f0_str}] {seg.zh}", flush=True)
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
