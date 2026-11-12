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


# Fallback list of 100% :free OpenRouter models if offline during startup
FALLBACK_FREE_MODELS: list[tuple[str, int, int]] = [
    ("google/gemma-3-27b-it:free", 131072, 8192),
    ("deepseek/deepseek-chat-v3-0324:free", 131072, 8192),
    ("deepseek/deepseek-chat:free", 131072, 8192),
    ("qwen/qwen3-235b-a22b:free", 131072, 8192),
    ("qwen/qwen-2.5-72b-instruct:free", 131072, 8192),
    ("meta-llama/llama-3.3-70b-instruct:free", 131072, 8192),
    ("mistralai/mistral-small-3.1-24b-instruct:free", 96000, 8192),
    ("google/gemma-3-12b-it:free", 131072, 8192),
    ("openrouter/free", 131072, 4096),
]


def _multilingual_quality_tier(model_id: str) -> int:
    """Rank multilingual JA->VI instruction-following quality among high-token :free models."""
    m = model_id.lower()
    if "gemma-3-27b" in m or "deepseek-chat" in m or "qwen3-235b" in m or "qwen-2.5-72b" in m:
        return 3
    if any(k in m for k in ("llama-3.3-70b", "qwen3-32b", "mistral-small", "gemini-2.0-flash", "gemma-3-12b", "nemotron")):
        return 2
    if any(k in m for k in ("gemma", "deepseek", "qwen", "llama-3", "mistral")):
        return 1
    return 0


def _ensure_free_slug(model_id: str) -> str:
    """Ensure a model ID is strictly a :free model (never a paid model)."""
    m = (model_id or "").strip()
    if not m:
        return "google/gemma-3-27b-it:free"
    if m == "openrouter/free" or m.endswith(":free"):
        return m
    return f"{m}:free"


def discover_free_models_by_token_limit(
    base_url: str, configured_model: str
) -> tuple[list[str], dict[str, tuple[int, int]]]:
    """Query OpenRouter's live /models catalog, filter STRICTLY for 100% :free models ($0 prompt & $0 completion),
    and sort them by highest token capacity (context_length + max_completion_tokens) and JA->VI quality.
    NEVER includes any paid model."""
    token_info: dict[str, tuple[int, int]] = {}
    candidates: list[tuple[str, int, int, int]] = []  # (model_id, tier, context_len, max_out)

    if "openrouter.ai" in (base_url or ""):
        try:
            url = base_url.rstrip("/") + "/models"
            req = urllib.request.Request(url, headers={"User-Agent": "RT-Subs/1.2", "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=8) as resp:
                data = json.loads(resp.read().decode("utf-8", errors="ignore"))

            for item in data.get("data", []):
                mid = str(item.get("id", "")).strip()
                if not mid:
                    continue
                pricing = item.get("pricing") or {}
                try:
                    p_cost = float(pricing.get("prompt", 1.0))
                    c_cost = float(pricing.get("completion", 1.0))
                except (ValueError, TypeError):
                    p_cost, c_cost = 1.0, 1.0

                # Strictly require $0.00 prompt AND $0.00 completion AND (:free suffix or openrouter/free)
                if p_cost != 0.0 or c_cost != 0.0:
                    continue
                if not (mid.endswith(":free") or mid == "openrouter/free"):
                    continue

                lower_id = mid.lower()
                # Skip tiny/unsuitable models (vision-only, guard, moderation, embedding, 1B-4B models, or slow reasoning-only r1)
                if any(
                    skip in lower_id
                    for skip in ("guard", "moderation", "embed", "-1b", "-2b", "-3b", "-4b", "vl-", "-vl", "vision")
                ):
                    continue

                ctx_len = int(item.get("context_length") or 32768)
                top_prov = item.get("top_provider") or {}
                max_out = int(top_prov.get("max_completion_tokens") or min(ctx_len, 8192))
                tier = _multilingual_quality_tier(mid)
                # Deprioritize slow chain-of-thought reasoning models (like r1 / qwqw) behind fast instruct models
                if "r1" in lower_id or "thinking" in lower_id or "qwq" in lower_id:
                    tier = -1

                token_info[mid] = (ctx_len, max_out)
                candidates.append((mid, tier, ctx_len, max_out))
        except Exception as e:
            print(f"[Config] Warning: Could not fetch live OpenRouter /models ({e}); using built-in :free list.", flush=True)

    if not candidates:
        for mid, ctx_len, max_out in FALLBACK_FREE_MODELS:
            token_info[mid] = (ctx_len, max_out)
            candidates.append((mid, _multilingual_quality_tier(mid), ctx_len, max_out))

    # Sort descending by:
    # 1. Whether it's a fast instruct model (tier >= 1) with large token context (>= 64k)
    # 2. Context length + max completion tokens (most tokens first!)
    # 3. Multilingual JA->VI quality tier
    candidates.sort(
        key=lambda x: (
            1 if (x[1] >= 1 and x[2] >= 64000) else (0 if x[1] >= 0 else -1),
            x[1],
            x[2],
            x[3],
        ),
        reverse=True,
    )

    chain = [c[0] for c in candidates]

    # If user explicitly set a :free model in .env and it exists in live free models, put it first
    user_free = _ensure_free_slug(configured_model)
    if user_free in chain and user_free != "google/gemma-3-27b-it:free":
        chain.remove(user_free)
        chain.insert(0, user_free)

    # Always include OpenRouter's official zero-cost auto-router "openrouter/free" at the end
    if "openrouter/free" not in chain:
        chain.append("openrouter/free")
        token_info.setdefault("openrouter/free", (131072, 4096))

    return chain, token_info


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
    prefer_free_models: bool = True  # Strictly 100% :free models (never use paid)
    chars_per_sec: float = 28.0
    base_url: str = field(
        default_factory=lambda: os.environ.get("TRANSLATION_BASE_URL", "") or "https://openrouter.ai/api/v1"
    )
    api_key: str = field(default_factory=_resolve_api_key)
    model: str = field(
        default_factory=lambda: _ensure_free_slug(
            os.environ.get("TRANSLATION_MODEL", "") or "google/gemma-3-27b-it:free"
        )
    )
    model_chain: list[str] = field(default_factory=list)
    model_token_info: dict[str, tuple[int, int]] = field(default_factory=dict)
    context_pairs: int = 8  # Compact 8-line dialogue window to keep prompt under ~950 tokens
    story_lookahead_sec: float = 300.0
    bible_every: int = 6
    transcript_max_chars: int = 6000  # Used only once during Story Bible generation (never sent in translate calls!)

    def __post_init__(self) -> None:
        if not self.model_chain:
            self.model_chain, self.model_token_info = discover_free_models_by_token_limit(self.base_url, self.model)
            if self.model_chain:
                self.model = self.model_chain[0]

    def format_model_info(self, model_id: str) -> str:
        ctx, out = self.model_token_info.get(model_id, (0, 0))
        if ctx > 0:
            return f"{model_id} [ctx={ctx // 1000}k, max_out={out}]"
        return model_id

    def adapt_to_token_limit(self, affordable_tokens: int) -> None:
        """Dynamically shrink token_ceiling, max_lines_per_batch, and mega_batch_sec
        whenever a provider reports 'can only afford X tokens'."""
        safe_tokens = max(250, int(affordable_tokens * 0.85))
        if safe_tokens >= self.token_ceiling:
            return
        self.token_ceiling = safe_tokens
        self.bible_max_tokens = min(self.bible_max_tokens, max(250, int(safe_tokens * 0.75)))
        new_max_lines = max(4, (safe_tokens - 120) // 55)
        self.max_lines_per_batch = min(self.max_lines_per_batch, new_max_lines)
        new_batch_sec = max(self.chunk_target_sec, round(float(self.max_lines_per_batch * 3.0), 1))
        self.mega_batch_sec = min(self.mega_batch_sec, new_batch_sec)
        print(
            f"[Adaptive Token Scaler] Adjusted to token limit ({affordable_tokens} tokens): "
            f"token_ceiling={self.token_ceiling}, max_lines_per_batch={self.max_lines_per_batch}, "
            f"mega_batch_sec={self.mega_batch_sec:.0f}s",
            flush=True,
        )

    def adapt_to_prompt_limit(self, allowed_prompt_tokens: int) -> None:
        """Dynamically shrink prompt context if a model has a small prompt token cap."""
        self.context_pairs = max(3, min(self.context_pairs, 4))
        self.transcript_max_chars = max(2000, min(self.transcript_max_chars, allowed_prompt_tokens * 2))
        print(
            f"[Adaptive Prompt Scaler] Shrunk prompt context to fit {allowed_prompt_tokens} prompt tokens "
            f"(context_pairs={self.context_pairs}, transcript_max_chars={self.transcript_max_chars})",
            flush=True,
        )
