from __future__ import annotations

import json
import os
import urllib.request
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
            val = value.strip().strip("'\"")
            if val and "paste-your-" not in val:
                os.environ.setdefault(key.strip(), val)


_load_env()


def _resolve_api_key() -> str:
    for env_name in ("OPENROUTER_API_KEY", "OPENAI_API_KEY"):
        val = os.environ.get(env_name, "").strip().strip("'\"")
        if val and "paste-your-" not in val and val != "dummy":
            return val
    return "dummy"


# Curated priority order of uncensored / high-quality multilingual :free OpenRouter models for JA -> VI
PREFERRED_FREE_MODELS = [
    "google/gemma-3-27b-it:free",
    "deepseek/deepseek-chat-v3-0324:free",
    "deepseek/deepseek-chat:free",
    "qwen/qwen-2.5-72b-instruct:free",
    "qwen/qwen3-235b-a22b:free",
    "qwen/qwen3-32b:free",
    "meta-llama/llama-3.3-70b-instruct:free",
    "mistralai/mistral-small-3.1-24b-instruct:free",
    "mistralai/mistral-nemo:free",
    "google/gemma-3-12b-it:free",
    "google/gemini-2.0-flash-exp:free",
]


def build_model_chain(base_url: str, configured_model: str, prefer_free: bool = True) -> list[str]:
    """Build an ordered list of OpenRouter models prioritizing :free models (0 credits/token).
    Queries OpenRouter's live /models list so only currently active :free models are used,
    ranked by Japanese->Vietnamese translation quality."""
    chain: list[str] = []
    cfg = (configured_model or "").strip()

    # If prefer_free is enabled and user's .env has a non-:free slug (e.g. google/gemma-3-27b-it),
    # try its :free variant first!
    if prefer_free and cfg:
        free_variant = cfg if cfg.endswith(":free") else f"{cfg}:free"
        chain.append(free_variant)

    # Query live OpenRouter /models endpoint to discover currently active :free models
    live_free: set[str] = set()
    if "openrouter.ai" in (base_url or ""):
        try:
            url = base_url.rstrip("/") + "/models"
            req = urllib.request.Request(url, headers={"User-Agent": "RT-Subs/1.2", "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=6) as resp:
                data = json.loads(resp.read().decode("utf-8", errors="ignore"))
            for item in data.get("data", []):
                mid = str(item.get("id", "")).strip()
                pricing = item.get("pricing") or {}
                is_zero = str(pricing.get("prompt", "1")) in ("0", "0.0") and str(pricing.get("completion", "1")) in (
                    "0",
                    "0.0",
                )
                if mid and (mid.endswith(":free") or is_zero):
                    # Skip vision-only / guard / tiny 1B-3B models
                    lower_id = mid.lower()
                    if any(skip in lower_id for skip in ("guard", "moderation", "embed", "-1b", "-3b", "-vl-")):
                        continue
                    live_free.add(mid)
        except Exception:
            pass

    if prefer_free:
        # 1. Add preferred :free models that are confirmed live (or all preferred if offline check failed)
        for m in PREFERRED_FREE_MODELS:
            if (not live_free or m in live_free) and m not in chain:
                chain.append(m)
        # 2. Add any other live :free models from major families (gemma, deepseek, qwen, llama, mistral)
        for m in sorted(live_free):
            if m not in chain and any(fam in m.lower() for fam in ("gemma-3", "deepseek", "qwen", "llama-3", "mistral")):
                chain.append(m)

    # Finally add the exact configured model from .env as fallback (e.g. paid google/gemma-3-27b-it)
    if cfg and cfg not in chain:
        chain.append(cfg)
    if not chain:
        chain.append("google/gemma-3-27b-it:free")

    # If live_free succeeded, filter out any non-existent :free slug at index 0 unless it's in live_free
    if live_free:
        filtered = [m for m in chain if (not m.endswith(":free") or m in live_free)]
        if filtered:
            chain = filtered

    return chain


@dataclass
class Settings:
    workspace: Path = ROOT / "workspace"
    whisper_model: str = os.environ.get("WHISPER_MODEL", "large-v3-turbo")
    whisper_compute: str = os.environ.get("WHISPER_COMPUTE", "int8_float16")
    source_language: str = os.environ.get("SOURCE_LANGUAGE", "ja")
    chunk_min_sec: float = 10.0
    chunk_target_sec: float = float(os.environ.get("FIRST_CHUNK_SECONDS", "15.0"))  # 15s initial chunk on start/seek
    chunk_max_sec: float = 20.0
    mega_batch_sec: float = float(os.environ.get("MEGA_BATCH_SECONDS", "150.0"))  # 150s (2.5m) Mega-Batch after first chunk
    max_lines_per_batch: int = int(os.environ.get("MAX_LINES_PER_BATCH", "50"))  # Dynamically scales with token_ceiling
    bible_max_tokens: int = int(os.environ.get("BIBLE_MAX_TOKENS", "600"))  # Compact 600-token Story Bible
    token_ceiling: int = int(os.environ.get("MAX_COMPLETION_TOKENS", "4096"))  # Auto-adjusts if OpenRouter returns 402
    prefer_free_models: bool = os.environ.get("PREFER_FREE_MODELS", "1").strip().lower() not in ("0", "false", "no")
    chars_per_sec: float = 28.0
    base_url: str = field(
        default_factory=lambda: os.environ.get("TRANSLATION_BASE_URL", "") or "https://openrouter.ai/api/v1"
    )
    api_key: str = field(default_factory=_resolve_api_key)
    model: str = field(
        default_factory=lambda: os.environ.get("TRANSLATION_MODEL", "") or "google/gemma-3-27b-it:free"
    )
    model_chain: list[str] = field(default_factory=list)
    context_pairs: int = 14
    story_lookahead_sec: float = 300.0
    bible_every: int = 6
    transcript_max_chars: int = 16000

    def __post_init__(self) -> None:
        if not self.model_chain:
            self.model_chain = build_model_chain(self.base_url, self.model, self.prefer_free_models)
            if self.model_chain:
                self.model = self.model_chain[0]

    def adapt_to_token_limit(self, affordable_tokens: int) -> None:
        """Dynamically shrink token_ceiling, max_lines_per_batch, and mega_batch_sec
        whenever OpenRouter reports 'can only afford X tokens'."""
        safe_tokens = max(250, int(affordable_tokens * 0.85))
        if safe_tokens >= self.token_ceiling:
            return
        self.token_ceiling = safe_tokens
        self.bible_max_tokens = min(self.bible_max_tokens, max(250, int(safe_tokens * 0.75)))
        # Each translated JSON line {"id":0,"vi":"..."} takes ~50-55 tokens + 120 overhead
        new_max_lines = max(4, (safe_tokens - 120) // 55)
        self.max_lines_per_batch = min(self.max_lines_per_batch, new_max_lines)
        # ~1 line every 3 seconds of video on average -> scale mega_batch_sec to match
        new_batch_sec = max(self.chunk_target_sec, round(float(self.max_lines_per_batch * 3.0), 1))
        self.mega_batch_sec = min(self.mega_batch_sec, new_batch_sec)
        print(
            f"[Adaptive Token Scaler] Adjusted to credit/token limit ({affordable_tokens} tokens): "
            f"token_ceiling={self.token_ceiling}, max_lines_per_batch={self.max_lines_per_batch}, "
            f"mega_batch_sec={self.mega_batch_sec:.0f}s, bible_max_tokens={self.bible_max_tokens}",
            flush=True,
        )
