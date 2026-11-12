# Real-Time Japanese-to-Vietnamese Subtitle System (`rtsubs`) 🎬🇻🇳

A fast, non-intrusive real-time Japanese-to-Vietnamese subtitle system for Windows 10 PCs (supporting all websites including HLS `.m3u8` / 4-second `.ts` streaming sites, YouTube, Bilibili, TikTok, etc.).

---

## 📦 Zero-Build Download & Run (via GitHub Releases)

Every time you push to GitHub, **GitHub Actions (`.github/workflows/release.yml`)** automatically packages and publishes ready-to-download ZIP files under your repository's **Releases** tab (tag `latest`) and **Actions** artifacts:

1. Download **`rtsubs-windows10-ready.zip`** from your GitHub **Releases** page and extract it on your Windows 10 PC.
2. **Load the Browser Extension**:
   - Open Chrome (`chrome://extensions`) or Edge (`edge://extensions`).
   - Turn on **Developer mode** (top right).
   - Click **Load unpacked** and select the extracted **`extension`** folder.
3. **Double-Click `run_server.bat`**:
   - On first run, `run_server.bat` automatically creates your `.env` file (and opens Notepad so you can paste your `OPENAI_API_KEY`), auto-installs any missing Python packages into a local `.venv` (or uses your existing `rtsubs` Conda environment), and starts the server at `http://127.0.0.1:8765`.
4. Open any video website and click **`🇻🇳 VI · Phụ đề`** in the top-left corner of the video!

---

## 🌟 Key Features

- **Subtitle-Only (Zero Audio Interruption):** Keeps original video audio 100% intact and **never pauses or stalls your video**. When you seek/scrub the timeline, the server automatically jumps its pointer to your current playback timestamp (`video.currentTime`) and catches up in real time.
- **Works on `.m3u8` / 4-Second `.ts` Streaming Sites (`<all_urls>`):** Intercepts master/media `.m3u8` playlists in the page context so sites serving 4-second `.ts` segments stream smoothly via real-time windowed `ffmpeg -ss` decoding instead of cutting sentences mid-word.
- **Whisper ASR Tuned for Soft / Breathy Japanese Speech:** Uses `faster-whisper` (`large-v3-turbo`, `language="ja"`) with sensitive `silero-vad` parameters and a Japanese conversational/intimate initial prompt so whispered lines, sighs, and short exclamations (`あっ`, `だめ`, `気持ちいい`, `いく`, etc.) are captured accurately.
- **Sensual, Context-Aware 18+ Vietnamese LLM Translation:** Powered by OpenRouter / OpenAI-compatible APIs. Builds a live "Story Bible" of character relationships and pronouns (`anh - em`, `chị - em`, `thầy - em`, etc.) and translates dialogue into natural, expressive, uncensored Vietnamese.
- **Draggable Subtitle Bar:** Drag anywhere on the video, scroll the mouse wheel to resize font, or double-click to reset position.

---

## ⚙️ `.env` Configuration

```env
TRANSLATION_BASE_URL=https://openrouter.ai/api/v1
OPENAI_API_KEY=your_api_key_here
TRANSLATION_MODEL=google/gemma-3-27b-it
SOURCE_LANGUAGE=ja
```

---

## 📂 Project Structure

```
real-time-video-subs/
├── .github/workflows/
│   └── release.yml             # Auto-generates Windows 10 & Extension ZIPs on push
├── extension/                  # Chromium Extension (Manifest V3 - <all_urls>)
│   ├── manifest.json           # Extension configuration & universal permissions
│   ├── content.js              # Non-pausing overlay UI & playhead catch-up sync
│   ├── page_hook.js            # .m3u8 playlist & MSE SourceBuffer interception
│   └── test/                   # Local HTML test fixture
├── server/
│   └── rtsubs/                 # Local Python FastAPI + Whisper + LLM server
│       ├── server.py           # FastAPI endpoints & windowed .m3u8 stream runner
│       ├── pipeline.py         # Subtitle-only ASR & translation orchestrator
│       ├── asr.py              # Faster-Whisper ASR tuned for Japanese soft speech
│       ├── chunker.py          # Silero VAD sentence chunker
│       ├── translate.py        # 18+ sensual Vietnamese LLM translator + Story Bible
│       ├── run_file.py         # CLI tool to generate .srt/.json for local videos
│       └── config.py           # Application settings
├── run_server.bat              # 1-click Windows 10 auto-setup & launcher
├── requirements.txt            # Python dependencies
└── .env.example                # Example environment configuration
```
