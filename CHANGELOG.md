# Changelog — RT-Subs (Japanese ➔ Vietnamese 18+ Real-Time Subtitles)

## Latest Update Highlights (v1.2)
- **Strict 100% `:free` Highest-Token Model Selector (`config.py` & `translate.py`)**:
  - **100% `$0` `:free` Models Only (Never Uses Paid Models)**: Queries OpenRouter's live `/api/v1/models` catalog at startup, filters strictly for `$0` prompt & `$0` completion `:free` models, and sorts them by **highest token capacity (`context_length` + `max_completion_tokens`)** and multilingual JA➔VI quality (`google/gemma-3-27b-it:free`, `deepseek/deepseek-chat-v3-0324:free`, `qwen/qwen3-235b-a22b:free`, `meta-llama/llama-3.3-70b-instruct:free`, etc., plus `openrouter/free`). Even if your existing `.env` has a non-`:free` model name, paid models are strictly blocked so your credit balance never decreases.
  - **85% Smaller Translation Prompt (~950 Tokens vs 6,576 Tokens)**: Removed the redundant 5,000-token raw movie transcript from `translate()` calls (since the Webpage Plot Summary + 600-token Story Bible + recent dialogue lines already provide full context).
  - **Dynamic `max_tokens` & Automatic Prompt/Token Scaling**: Calculates `max_tokens` from the exact line count (`~55 tokens × lines + 120`) and automatically adapts if any provider reports a prompt or completion token limit.
- **Adaptive 15s First-Chunk + 150s Mega-Batch Engine (`server.py`, `pipeline.py`, `translate.py`)**:
  - **Tier 1 (Instant 15s Playhead Chunk)**: Processes a **15-second** chunk first when starting a video or seeking to an untranslated timestamp so subtitles appear on screen in **~1.5 seconds**.
  - **Tier 2 (150s / 2.5-Minute Mega-Batches)**: Immediately after the playhead chunk is ready, groups consecutive chunks into one contiguous pass—downloading HLS segments in parallel (8 threads) and transcribing contiguous audio in a single Faster-Whisper call.
- **Male vs. Female Voice Pitch Recognition (`asr.py` & `translate.py`)**:
  - Analyzes fundamental voice frequency ($F_0$ in Hz) + Japanese lexical markers on every Whisper segment to classify each line as **Male (`Giọng Nam`)** or **Female (`Giọng Nữ`)**.
- **Video Title, Code & Plot Summary Scraper + Persistent Cache by Video Code (`MEYD-916`)**:
  - Automatically reads the movie code, title, and plot summary from `missav` and caches translated subtitles in both browser `localStorage` and `workspace/cache/<CODE>.json`.
- **Versioned GitHub Releases with Update Log (`.github/workflows/release.yml`)**:
  - Every push to GitHub creates a brand-new versioned release (`v1.2.<build>`) with an automatic changelog and commit diff.
