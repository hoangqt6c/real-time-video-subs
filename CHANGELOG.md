# Changelog — RT-Subs (Japanese ➔ Vietnamese 18+ Real-Time Subtitles)

## Latest Update Highlights (v1.2)
- **Adaptive 15s First-Chunk + 150s Mega-Batch Engine (`server.py`, `pipeline.py`, `translate.py`, `config.py`)**:
  - **Tier 1 (Instant 15s Playhead Chunk)**: Processes a **15-second** chunk first when starting a video or seeking to an untranslated timestamp so subtitles appear on screen in **~1.5 seconds**.
  - **Tier 2 (150s / 2.5-Minute Mega-Batches)**: Immediately after the playhead chunk is ready, groups **10 consecutive chunks (`150 seconds` of audio)** into one contiguous pass—downloading HLS segments in parallel (8 threads), transcribing contiguous audio in a single Faster-Whisper call, and translating up to **50 Japanese lines in a single OpenRouter request (`max_tokens=8192`)** with automatic missing-ID recovery.
  - Reduces OpenRouter API requests by **10x–15x** (a 2-hour movie drops from 720 calls to ~48 calls) and eliminates sentence cutoffs across chunk boundaries.
- **Male vs. Female Voice Pitch Recognition (`asr.py` & `translate.py`)**:
  - Analyzes fundamental voice frequency ($F_0$ in Hz) + Japanese lexical markers on every Whisper segment to classify each line as **Male (`Giọng Nam`)** or **Female (`Giọng Nữ`)**.
  - Passes `"speaker": "Nam" | "Nữ"` to OpenRouter so Vietnamese pronouns (`"anh - em"`, `"em - anh"`, `"chị - em"`, `"thầy - em"`) accurately match the person speaking.
- **Video Title, Code & Plot Summary Scraper (`extension/content.js` & `translate.py`)**:
  - Automatically reads the movie code (e.g. `MEYD-916`, `STARS-359`), full title (`<h1>`), plot synopsis (`div.mb-1.text-secondary.break-all` on `missav`), and cast/genre tags from the webpage DOM.
  - Feeds the plot summary directly into the Story Bible and translation prompt from Chunk 0 so subtitles reflect the exact storyline (NTR, neighbor swap, office roleplay, etc.) and character names.
- **Persistent Subtitle & Whisper Cache by Video Code (`MEYD-916`)**:
  - Saves translated subtitles (with exact `start`/`end` timestamps) and Whisper ASR segments in **both** browser `localStorage` (`rtsubs_subcache_<CODE>`) and PC disk cache (`workspace/cache/<CODE>.json`).
  - Reopening any previously watched video loads existing subtitles **instantly (0s delay)** without re-running Whisper, and automatically resumes translating only the remaining untranslated chunks.
- **Versioned GitHub Releases with Update Log (`.github/workflows/release.yml`)**:
  - Every push to GitHub creates a brand-new versioned release (`v1.2.<build>`) with an automatic changelog and commit diff instead of overwriting a single `latest` release.

---

## Previous Updates
### v1.1 — Python HLS Segment Streamer & 3-Step Live Console Logs
- **Bypassed FFmpeg 7.1 `.jpeg` Segment Block (`pipeline.py`)**: Added `HlsPlaylist` to download disguised `.jpeg`/`.png`/`.ts` HLS segments on `surrit.com` (`missav.ws`) directly in Python and pipe raw MPEG-TS bytes to `ffmpeg -i pipe:0`.
- **3-Step Live Console Logging**: Added real-time terminal logs for `[Step 1/3 - Audio]`, `[Step 2/3 - Whisper JA]`, and `[Step 3/3 - OpenRouter VI]`.
- **Windows 10 `run_server.bat` Hardening**: Removed parenthesized `if (...)` blocks to prevent crashes on `Program Files (x86)` paths and added `HF_HUB_DISABLE_SYMLINKS_WARNING=1`.

### v1.0 — Subtitle-Only Windows 10 Edition & Extension Site Whitelist
- Stripped all dubbing/TTS/Demucs dependencies for fast, lightweight subtitle-only execution.
- Added Extension Popup with 1-click website whitelist (`popup.html` / `popup.js`).
- Retuned Faster-Whisper (`ja`) and OpenRouter prompts for uncensored, natural 18+ Vietnamese subtitles.
