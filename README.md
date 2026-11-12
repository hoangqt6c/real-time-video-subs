# Real-Time Japanese-to-Vietnamese Subtitle System (`rtsubs`) 🎬🇻🇳

A fast, non-intrusive real-time Japanese-to-Vietnamese subtitle system for Windows 10 PCs (supporting HLS `.m3u8` / 4-second `.ts` streaming sites, MP4 streams, YouTube, etc. **only on the specific websites you enable**).

---

## 📦 Zero-Build Download & Run (via GitHub Releases)

Every time you push to GitHub, **GitHub Actions (`.github/workflows/release.yml`)** automatically packages and publishes ready-to-download ZIP files under your repository's **Releases** tab (tag `latest`) and **Actions** artifacts:

1. Download **`rtsubs-windows10-ready.zip`** from your GitHub **Releases** page and extract it on your Windows 10 PC.
2. **Load the Browser Extension**:
   - Open Chrome (`chrome://extensions`) or Edge (`edge://extensions`).
   - Turn on **Developer mode** (top right).
   - Click **Load unpacked** and select the extracted **`extension`** folder.
3. **Enable Only the Websites You Want**:
   - By default, the extension stays completely off on all websites.
   - Open a video website where you want subtitles, click the **RT-Subs (`🇻🇳`) icon** in your browser toolbar, and click **"+ Bật phụ đề trên [tên miền]"** (or type any domain into the list).
   - The **`🇻🇳 VI · Phụ đề`** button will immediately appear on that site's videos!
4. **Double-Click `run_server.bat`**:
   - On first run, `run_server.bat` automatically creates your `.env` file (and opens Notepad so you can paste your `OPENAI_API_KEY`), auto-installs any missing Python packages into a local `.venv` (or uses your existing `rtsubs` Conda environment), and starts the server at `http://127.0.0.1:8765`.

---

## 🌟 Key Features

- **Site-Specific Whitelist Popup (`popup.html`):** Only runs on websites you explicitly enable via the 1-click toolbar popup menu.
- **Subtitle-Only (Zero Audio Interruption):** Keeps original video audio 100% intact and **never pauses or stalls your video**. When you seek/scrub the timeline, the server automatically jumps its pointer to your current playback timestamp (`video.currentTime`) and catches up in real time.
- **Works on `.m3u8` / 4-Second `.ts` Streaming Sites:** Intercepts master/media `.m3u8` playlists in the page context so sites serving 4-second `.ts` segments stream smoothly via real-time windowed `ffmpeg -ss` decoding instead of cutting sentences mid-word.
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
