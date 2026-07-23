# YouTube AI Manager

> A Python-based automation tool that finds viral TikTok clips, compiles them, generates AI-powered SEO, and publishes to YouTube — all driven from a Telegram bot on your phone.

Built with **Claude (Anthropic)**, **ElevenLabs**, **FFmpeg**, **python-telegram-bot**, and the **YouTube Data API v3**.

---

## Table of Contents

- [What it does](#what-it-does)
- [How it works](#how-it-works)
- [Content formats](#content-formats)
- [Tech stack](#tech-stack)
- [Prerequisites](#prerequisites)
- [Installation](#installation)
- [Configuration](#configuration)
- [YouTube OAuth2 setup](#youtube-oauth2-setup)
- [Telegram bot setup](#telegram-bot-setup)
- [Running the application](#running-the-application)
- [Running as a background service (macOS)](#running-as-a-background-service-macos)
- [Bot commands reference](#bot-commands-reference)
- [Project structure](#project-structure)
- [Known limitations](#known-limitations)
- [Security](#security)

---

## What it does

YouTube AI Manager is a personal automation platform for YouTube Shorts creators. Instead of manually hunting for clips, editing videos, writing titles, and scheduling uploads — you do it all through a Telegram conversation.

**Main capabilities:**

- **Clip discovery** — searches TikTok by hashtag, free text, or pasted URLs. Filters by duration, views, and deduplicates against your upload history
- **Smart ordering** — you pick the clip order manually, or ask Claude to arrange them for maximum retention
- **Video compilation** — downloads and merges clips with FFmpeg, crops to vertical 9:16
- **5 AI-generated content formats** — compilations, text-on-video, image-question reels, story reels, "which would you pick?" reels
- **SEO generation** — Claude analyzes the video visually and writes title, description, and tags
- **Thumbnail creation** — extracts the best frame and overlays AI-generated text with Pillow
- **Voice intro** — optionally adds a short pitch-shifted voice intro ("Hey, did you see this viral video?") via ElevenLabs
- **Background music** — searches YouTube for music, previews 25 seconds, downloads as MP3, mixes at low volume
- **Smart scheduling** — recommends optimal publish times, skips weekends if configured, supports immediate or scheduled upload
- **Fully headless** — runs as a background daemon, no screen needed after setup

---

## How it works

### Telegram bot workflow (`/trabajar`)

```
/trabajar
  ↓
Format selection
  ├── YouTube Video (landscape, 5 clips)
  ├── Short / Reel (vertical, 4 clips)
  └── Both formats

  ↓
Content source
  ├── Search by hashtag + category
  ├── Search by free text
  └── Paste TikTok URLs directly

  ↓
Country filter (Global / Argentina / Mexico / Brazil / Colombia / Chile / Spain / USA)

  ↓
Bot searches, filters and deduplicates clips
  → Shows thumbnails + title + views + duration for each clip

  ↓
You approve clips (or swap individual ones)

  ↓
Clip ordering
  ├── Claude chooses the order (AI-ranked for retention)
  ├── You pick the order manually (one by one)
  └── Keep current order

  ↓
Background music selection
  ├── Search YouTube for a song (30s preview)
  ├── Choose from downloaded library
  └── No music

  ↓
Compilation (FFmpeg — real-time progress updates every 30s)

  ↓
Preview: video + thumbnail + SEO
  [✅ Upload to YouTube]  [🔄 Recompile]
  [✏️ Regenerate SEO]
  [🎙️ Add voice intro]

  ↓
Schedule selection
  ├── Upload now
  ├── Recommended time (calculated from peak hours)
  ├── Preset slots (today/tomorrow at peak times)
  └── Custom date/time (parsed by Claude from natural language)

  ↓
Uploaded + YouTube link returned
```

### AI-generated format workflows (`/imagen`, `/historia`, `/eleccion`, `/texto`)

Each format follows a shorter flow:

```
Command → Choose topic/category → Claude generates content
→ Approve or regenerate → Choose music → Video created
→ Preview + [Upload] [Discard] [Add voice intro]
→ Smart scheduling → Upload
```

### Automated daily workflow

Every weekday at a configured time (default: 18:00 Argentina), the bot runs the full workflow automatically:
searches clips, compiles, generates SEO and thumbnail, and uploads — no interaction needed.

---

## Content formats

### 1. TikTok Compilation (`/trabajar`)
Downloads and merges 4–5 TikTok clips around a hashtag. Generates SEO from the video content using Claude's vision. Adds thumbnail.

### 2. Text-on-Video Short (`/texto`)
Claude generates a viral text (curiosity fact, opinion, poll, etc.) and renders it over an animated color background with FFmpeg `drawtext`. No downloads needed — generates in seconds.

**Available types:** curiosity, controversial opinion, poll, tip, story, myth vs. fact, ranking, confession

### 3. Image Question Reel (`/imagen`)
Claude writes a viral engagement question ("Would you rather...?"), downloads matching images from Pexels/Pixabay, and compiles them with the question as an animated overlay.

### 4. Story Reel (`/historia`)
Claude writes a first-person story ("My boss asked me to work on Sunday...") over a looping animated background. Designed for customer service / workplace content.

### 5. "Which Would You Pick?" Reel (`/eleccion`)
Claude generates 6 options for a category (cars, vacation spots, watches, etc.) along with a relational question ("If your partner knows you well, which car would they buy you?"). Downloads images, numbers each one, compiles with the question overlay. Tracks previously used options to avoid repetition.

**Available categories:** Cars, Houses, Vacations, Watches, Sneakers, Food, Tech, Pets, Airlines, Nightlife

### 6. Voice Narration Reel (`/narracion`)
Claude writes a short script, ElevenLabs turns it into a voiced narration, and Pexels stock clips + Wikipedia character images are stitched together to match the audio length exactly. Burns karaoke-style word-by-word subtitles with Whisper.

### 7. World Cup Predictions (`/pronosticos`)
Pulls the day's matches from the ESPN API, has Claude simulate predictions from 4 "AI personalities", and renders a 3-slide video (title, scoreboard table, CTA) over an animated dark background.

### Branding
Every reel generated by the bot or the local Compilador (vertical format) gets a small, low-opacity watermark (`MarcaDeAgua.png`) burned into the top-right corner via FFmpeg — see `modules/marca_agua.py`.

---

## Tech stack

| Layer | Technology |
|---|---|
| AI / LLM | [Claude](https://anthropic.com) (claude-sonnet-4-6, claude-haiku-4-5) |
| Voice synthesis | [ElevenLabs](https://elevenlabs.io) (eleven_multilingual_v2) |
| Video processing | [FFmpeg](https://ffmpeg.org) + [yt-dlp](https://github.com/yt-dlp/yt-dlp) |
| Image processing | [Pillow](https://python-pillow.org) |
| Bot framework | [python-telegram-bot](https://python-telegram-bot.org) v20+ (async) |
| Web dashboard | [Streamlit](https://streamlit.io) |
| YouTube upload | [YouTube Data API v3](https://developers.google.com/youtube/v3) (OAuth2) |
| Task scheduler | [APScheduler](https://apscheduler.readthedocs.io) |
| Image search | [Pexels API](https://www.pexels.com/api/) + [Pixabay API](https://pixabay.com/api/docs/) |
| Scene detection | [PySceneDetect](https://www.scenedetect.com) |
| Language | Python 3.10+ (asyncio, type hints) |

---

## Prerequisites

Before installing, make sure you have:

**System tools:**
- Python 3.10 or higher
- FFmpeg — `brew install ffmpeg` (macOS) / `sudo apt install ffmpeg` (Linux)
- yt-dlp — `brew install yt-dlp` (macOS) / `pip install yt-dlp` (Linux)

**API accounts (required):**
- [Anthropic](https://console.anthropic.com) — Claude API key
- [Google Cloud](https://console.cloud.google.com) — YouTube Data API v3 key + OAuth2 credentials
- [Telegram](https://t.me/BotFather) — Bot token + your Chat ID

**API accounts (optional but recommended):**
- [ElevenLabs](https://elevenlabs.io) — for voice intros and narration
- [Pexels](https://www.pexels.com/api/) — free stock images for reels
- [Pixabay](https://pixabay.com/api/docs/) — backup image source

---

## Installation

```bash
# 1. Clone the repository
git clone https://github.com/agumarini94/youtube-ai-manager.git
cd youtube-ai-manager

# 2. Create and activate a virtual environment
python3 -m venv venv
source venv/bin/activate          # macOS / Linux
# venv\Scripts\activate           # Windows

# 3. Install Python dependencies
pip install -r requirements.txt

# 4. Copy the environment template and fill in your keys
cp .env.example .env
# Edit .env with your API keys (see Configuration section below)

# 5a. Start the Streamlit web dashboard
streamlit run app.py

# 5b. Or start the Telegram bot (separate terminal)
python3 run_bot.py
```

---

## Configuration

Copy `.env.example` to `.env` and fill in each value:

```bash
cp .env.example .env
```

### Required variables

| Variable | Description | Where to get it |
|---|---|---|
| `ANTHROPIC_API_KEY` | Claude API key | [console.anthropic.com/settings/keys](https://console.anthropic.com/settings/keys) |
| `YOUTUBE_API_KEY` | YouTube Data API v3 key | Google Cloud Console → APIs & Services → Credentials |
| `YOUTUBE_CLIENT_SECRETS_FILE` | Path to OAuth2 JSON file | `client_secret.json` (see OAuth2 setup below) |
| `YOUTUBE_CHANNEL_ID` | Your channel ID | YouTube Studio → Settings → Channel info |
| `YOUTUBE_TARGET_CHANNEL` | Your channel name | Displayed in YouTube Studio header |
| `TELEGRAM_TOKEN` | Bot token | [@BotFather](https://t.me/BotFather) → `/newbot` |
| `TELEGRAM_CHAT_ID` | Your personal chat ID | [@userinfobot](https://t.me/userinfobot) |

### Optional variables

| Variable | Default | Description |
|---|---|---|
| `ELEVENLABS_API_KEY` | — | ElevenLabs key for voice intro and narration |
| `ELEVENLABS_VOICE_ID` | `21m00Tcm4TlvDq8ikWAM` | Voice ID (Rachel by default) |
| `ELEVENLABS_VOICE_ID_NARRACION` | same as above | Voice ID used specifically for `/narracion` reels |
| `PEXELS_API_KEY` | — | Free stock images/videos — [pexels.com/api](https://www.pexels.com/api/) |
| `PIXABAY_API_KEY` | — | Backup image source — [pixabay.com/api/docs](https://pixabay.com/api/docs/) |
| `HORA_AUTO` | `18` | Hour for daily auto-publish (Argentina timezone) |
| `MINUTO_AUTO` | `0` | Minute for daily auto-publish |

---

## YouTube OAuth2 setup

OAuth2 is required to upload videos. The YouTube Data API key alone is only for reading data.

**Step by step:**

1. Go to [console.cloud.google.com](https://console.cloud.google.com) and create a new project (or use an existing one)

2. Enable the **YouTube Data API v3**:
   - APIs & Services → Library → search "YouTube Data API v3" → Enable

3. Create OAuth2 credentials:
   - APIs & Services → Credentials → Create Credentials → OAuth client ID
   - Application type: **Desktop app**
   - Give it any name

4. Download the JSON file:
   - Click the download icon next to your new credential
   - Rename the file to `client_secret.json`
   - Place it in the project root (same folder as `app.py`)

5. Configure the consent screen:
   - APIs & Services → OAuth consent screen
   - User type: **External**
   - Add your Google account email as a **Test user**
   - (You don't need to publish the app)

6. Authenticate the first time:
   - Open the Streamlit app: `streamlit run app.py`
   - Go to **Subida a YouTube** tab
   - Click **Autenticar con Google** and follow the browser flow
   - A `token_youtube.json` file will be created automatically — this is your access token

> **Note:** The token expires periodically. Re-authenticate from the Streamlit app when uploads start failing.

---

## Telegram bot setup

1. Open Telegram and chat with [@BotFather](https://t.me/BotFather)
2. Send `/newbot`, follow the prompts, and copy the **bot token**
3. Chat with [@userinfobot](https://t.me/userinfobot) to get your **Chat ID**
4. Add both to your `.env` file:
   ```
   TELEGRAM_TOKEN=your_bot_token_here
   TELEGRAM_CHAT_ID=your_chat_id_here
   ```
5. Start the bot: `python3 run_bot.py`
6. Open Telegram, find your bot, and send `/trabajar`

> The bot only responds to your personal Chat ID — it ignores all other users by design.

---

## Running the application

### Streamlit web dashboard

```bash
source venv/bin/activate
streamlit run app.py
```

Opens at [http://localhost:8501](http://localhost:8501). Includes:
- TikTok clip browser and downloader
- Video compiler and editor
- Thumbnail generator
- SEO generator
- YouTube uploader
- Channel analytics
- Viral trends tracker
- Upload history
- Voice generator
- Publishing calendar
- Clip splitter

### Telegram bot (manual)

```bash
source venv/bin/activate
python3 run_bot.py
```

Starts the bot and the daily scheduler. Keep this terminal open or use the background service method below.

### Keeping the bot alive (optional — macOS only)

To prevent delays from Mac sleep mode:

```bash
caffeinate -i python3 run_bot.py &
```

`caffeinate -i` prevents the Mac from sleeping while the bot is running, which avoids Telegram connection drops.

---

## Running as a background service (macOS)

To run the bot automatically at login and keep it running in the background with `launchd`:

**1. Create the plist file:**

```bash
cat > ~/Library/LaunchAgents/com.youtubebot.plist << 'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.youtubebot</string>
    <key>ProgramArguments</key>
    <array>
        <string>/usr/bin/caffeinate</string>
        <string>-i</string>
        <string>/path/to/venv/bin/python3</string>
        <string>/path/to/youtube_ai_manager/run_bot.py</string>
    </array>
    <key>WorkingDirectory</key>
    <string>/path/to/youtube_ai_manager</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string>
    </dict>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>/tmp/youtubebot.log</string>
    <key>StandardErrorPath</key>
    <string>/tmp/youtubebot_error.log</string>
</dict>
</plist>
EOF
```

Replace `/path/to/` with your actual paths.

**2. Load the service:**

```bash
launchctl load ~/Library/LaunchAgents/com.youtubebot.plist
launchctl start com.youtubebot
```

**3. Useful commands:**

```bash
launchctl list | grep youtubebot      # Check if running (shows PID)
launchctl stop com.youtubebot         # Stop
launchctl start com.youtubebot        # Start
tail -f /tmp/youtubebot_error.log     # Watch logs live
```

> **Important:** The `PATH` variable in the plist must include `/opt/homebrew/bin` so FFmpeg and yt-dlp are found. This is a common source of errors when running as a daemon.

---

## Bot commands reference

### Main commands

| Command | Description |
|---|---|
| `/trabajar` | Start the full TikTok → compile → upload workflow |
| `/urls <url1> <url2> ...` | Use specific TikTok URLs instead of searching |
| `/texto` | Create a text-on-video Short with Claude |
| `/imagen` | Create a viral image-question reel |
| `/historia` | Create a customer service story reel |
| `/eleccion` | Create a "which would you pick?" reel |
| `/narracion` | Create a voice-narrated reel with stock footage + karaoke subtitles |
| `/pronosticos` | Create a World Cup match predictions video |
| `/estado` | Show the current workflow state |
| `/cancelar` | Cancel any active workflow and reset |
| `/ping` | Check if the bot is alive |
| `/stats` | Show upload history stats |
| `/uso` | Show current ElevenLabs character usage |
| `/saltar` | Skip the topic prompt (let AI choose) |

### Workflow buttons (inline keyboard)

| Button | Available in | Description |
|---|---|---|
| ✅ Approve clips | `/trabajar` | Approve all clips and continue |
| 🚫 Clip N | `/trabajar` | Replace that specific clip with the next candidate |
| 🔄 Search others | `/trabajar` | Change category and search again |
| 🤖 Claude picks order | `/trabajar` | Let Claude rank clips for maximum retention |
| 🔢 I'll pick the order | `/trabajar` | Manually pick which clip goes first, second, etc. |
| 📋 Keep this order | `/trabajar` | Continue with current clip order |
| 🔍 Search song | Any format | Search YouTube for background music |
| 🔇 No music | Any format | Skip background music |
| 🎙️ Add voice intro | Any format | Add a short ElevenLabs voice intro at the start |
| 📤 Upload to YouTube | Any format | Schedule or upload immediately |
| 🔄 Recompile | `/trabajar` | Search for new clips from scratch |
| ✏️ Regenerate SEO | `/trabajar` | Ask Claude to write new title/description/tags |
| 🗑 Discard | Any format | Delete the video and reset |

---

## Project structure

```
youtube_ai_manager/
│
├── app.py                    # Streamlit web dashboard entry point
├── run_bot.py                # Telegram bot + APScheduler entry point
├── requirements.txt          # Python dependencies
├── .env.example              # Environment variable template
├── .gitignore
├── MarcaDeAgua.png            # Brand watermark burned into every reel
│
├── modules/
│   │
│   ├── telegram_bot.py       # Bot handlers, inline keyboards, workflow state
│   ├── workflow.py           # Headless orchestrator: compile → SEO → upload
│   ├── flujo_estado.py       # JSON-persisted state machine (survives restarts)
│   │
│   ├── selector_ia.py        # TikTok search, deduplication, Claude ranking
│   ├── downloader.py         # yt-dlp wrapper for YouTube and TikTok
│   ├── tiktok_feed.py        # TikTok clip browser (Streamlit)
│   │
│   ├── compilador.py         # FFmpeg video compilation pipeline
│   ├── video_editor.py       # Crop, speed, subtitles, effects
│   ├── clip_splitter.py      # Scene detection (PySceneDetect)
│   │
│   ├── texto_video.py        # Text-on-video Short generator
│   ├── imagen_reel.py        # Image-question reel generator
│   ├── historia_reel.py      # Story reel generator
│   ├── eleccion_reel.py      # "Which would you pick?" reel + history tracking
│   ├── narracion_reel.py     # Voice narration reel (ElevenLabs + Pexels + Whisper subtitles)
│   ├── pronosticos_mundial.py # World Cup predictions video (ESPN API + Claude)
│   ├── pexels_video.py       # Pexels stock video search/download for narration reels
│   ├── marca_agua.py         # Watermark overlay (FFmpeg) burned into every reel
│   │
│   ├── music_manager.py      # Music search, preview, download, mixing
│   ├── voice_gen.py          # ElevenLabs TTS — Streamlit UI
│   ├── voice_intro.py        # Short voice intro with pitch shift (headless)
│   ├── sync_audio.py         # Per-clip narration (Claude writes, ElevenLabs speaks)
│   │
│   ├── seo_gen.py            # Claude-powered SEO with vision analysis
│   ├── thumbnail_gen.py      # Frame extraction + text overlay (Pillow)
│   ├── uploader.py           # YouTube OAuth2 upload
│   │
│   ├── analyzer.py           # Channel analytics with Claude insights
│   ├── trends.py             # Viral trend discovery
│   ├── content_gen.py        # General AI content generation (Streamlit)
│   ├── calendario.py         # Publishing calendar and event planning
│   ├── historial.py          # Upload history tracker
│   ├── config.py             # Central configuration constants
│   └── proceso.py            # Step-by-step guided workflow (Streamlit)
│
├── data/                     # Runtime data — NOT committed to git
│   ├── flujo_estado.json     # Active workflow state
│   ├── historial_videos.json # Upload history
│   ├── tiktok_history.json   # Downloaded TikTok IDs (deduplication)
│   └── cache_seguidores.json # TikTok follower cache
│
├── music/                    # Background music library (MP3 files)
│   └── *.mp3
│
└── ytbot_clips/              # Temporary video files — NOT committed to git
    └── *.mp4
```

### Files not committed to git

The following are in `.gitignore` and must be created locally:

| File / Folder | Contains |
|---|---|
| `.env` | All API keys and tokens |
| `client_secret.json` | Google OAuth2 app credentials |
| `token_youtube.json` | YouTube access token (generated on first auth) |
| `data/` | Upload history and workflow state |
| `ytbot_clips/` | Compiled video output |
| `venv/` | Python virtual environment |

---

## Known limitations

- **Single user only** — the bot responds exclusively to the `TELEGRAM_CHAT_ID` in `.env`. There is no multi-user support
- **macOS / Homebrew paths** — FFmpeg and yt-dlp are referenced at `/opt/homebrew/bin/`. On Linux or Windows, you may need to update these paths in `modules/compilador.py`, `modules/music_manager.py`, and others, or ensure the binaries are on your system `PATH`
- **No test suite** — there are no automated tests. Breakages surface at runtime
- **Flat state machine** — workflow state is stored in a single JSON dict. Concurrent use is not safe
- **YouTube upload quota** — the YouTube Data API v3 has a daily upload limit of ~6 videos per project. If you hit this, wait 24 hours or create a new Google Cloud project
- **TikTok rate limits** — bulk searches can be throttled. The bot handles this by spacing requests, but very high search volumes may require delays
- **ElevenLabs character limit** — the free tier allows ~10,000 characters/month. Voice intro phrases are short (<10 words) to stay within limits

---

## Security

**Never commit these files:**

```
.env
client_secret.json
token_youtube.json
data/
```

These contain your API keys and OAuth2 tokens. The `.gitignore` already excludes them, but double-check before pushing.

**The bot is private by design.** Every handler checks `update.effective_chat.id != CHAT_ID` and silently ignores anyone who isn't you.

---

## Author

Built by [Agustin Marini](https://github.com/agumarini94).

Open to feedback, ideas, and collaboration.
