# Changelog — RT-Subs (Japanese ➔ Vietnamese 18+ Real-Time Subtitles)

## Latest Update Highlights (v1.2)
- **Free-First OpenRouter Model Router & Adaptive Token/Chunk Scaler (`config.py` & `translate.py`)**:
  - **Always Prioritizes `:free` OpenRouter Models First (`0` credits/token)**: Queries OpenRouter's live `/api/v1/models` list at startup and builds an automatic fallback chain of active `:free` models (`google/gemma-3-27b-it:free`, `deepseek/deepseek-chat-v3-0324:free`, `qwen/qwen-2.5-72b-instruct:free`, `meta-llama/llama-3.3-70b-instruct:free`, etc.). Automatically rotates to the next `:free` model if a model is rate-limited (`429`).
  - **Compact 600-Token Story Bible**: Shrunk Story Bible `max_tokens` from `4000` down to **`600` tokens** (`BIBLE_MAX_TOKENS=600`).
  - **Dynamic Right-Sized `max_tokens` & Automatic `402` Credit Adaptation**: Instead of requesting a fixed `8192` tokens, calculates `max_tokens` dynamically from the exact line count (`~55 tokens × lines + 120`). If OpenRouter ever reports `402 ... can only afford X`, the server parses `X` and automatically shrinks `token_ceiling`, `max_lines_per_batch`, and `mega_batch_sec` to fit.
- **Adaptive 15s First-Chunk + 150s Mega-Batch Engine (`server.py`, `pipeline.py`, `translate.py`)**:
  - **Tier 1 (Instant 15s Playhead Chunk)**: Processes a **15-second** chunk first when starting a video or seeking to an untranslated timestamp so subtitles appear on screen in **~1.5 seconds**.
  - **Tier 2 (150s / 2.5-Minute Mega-Batches)**: Immediately after the playhead chunk is ready, groups consecutive chunks into one contiguous pass—downloading HLS segments in parallel (8 threads) and transcribing contiguous audio in a single Faster-Whisper call.
- **Male vs. Female Voice Pitch Recognition (`asr.py` & `translate.py`)**:
  - Analyzes fundamental voice frequency ($F_0$ in Hz) + Japanese lexical markers on every Whisper segment to classify each line as **Male (`Giọng Nam`)** or **Female (`Giọng Nữ`)**.
- **Video Title, Code & Plot Summary Scraper + Persistent Cache by Video Code (`MEYD-916`)**:
  - Automatically reads the movie code, title, and plot summary from `missav` and caches translated subtitles in both browser `localStorage` and `workspace/cache/<CODE>.json`.
- **Versioned GitHub Releases with Update Log (`.github/workflows/release.yml`)**:
  - Every push to GitHub creates a brand-new versioned release (`v1.2.<build>`) with an automatic changelog and commit diff.
