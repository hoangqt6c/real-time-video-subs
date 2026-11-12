from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _load_env() -> None:
    env = ROOT / ".env"
    if not env.is_file():
        return
    for line in env.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, value = line.split("=", 1)
            val = value.strip()
            if val:
                os.environ.setdefault(key.strip(), val)


_load_env()


@dataclass
class Settings:
    workspace: Path = ROOT / "workspace"
    whisper_model: str = os.environ.get("WHISPER_MODEL", "large-v3-turbo")
    whisper_compute: str = os.environ.get("WHISPER_COMPUTE", "int8_float16")
    source_language: str = os.environ.get("SOURCE_LANGUAGE", "ja")
    chunk_min_sec: float = 6.0
    chunk_target_sec: float = 10.0
    chunk_max_sec: float = 14.0
    chars_per_sec: float = 28.0  # Comfortable subtitle reading budget (no TTS speed constraints)
    base_url: str = field(
        default_factory=lambda: os.environ.get("TRANSLATION_BASE_URL", "") or "https://openrouter.ai/api/v1"
    )
    api_key: str = field(
        default_factory=lambda: os.environ.get("OPENROUTER_API_KEY", "")
        or os.environ.get("OPENAI_API_KEY", "")
        or "dummy"
    )
    model: str = field(
        default_factory=lambda: os.environ.get("TRANSLATION_MODEL", "") or "google/gemma-3-27b-it"
    )
    context_pairs: int = 12
    story_lookahead_sec: float = 120.0  # background ASR stays up to 2 minutes ahead of playback
    bible_every: int = 3  # rebuild the story bible after this many newly transcribed chunks
    transcript_max_chars: int = 20000  # cap on the source transcript placed in the prompt
