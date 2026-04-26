# EchoPulsing Music Bot

Fast Telegram voice chat music bot focused on low-latency streaming and clean playback control.

## Features
- ⚡ Fast streaming with yt-dlp direct URLs (no full media download)
- 🎧 Voice chat playback powered by PyTgCalls
- 📜 Smart per-chat queue system
- 🔁 Loop modes: off, single, all
- 🔀 Shuffle support
- ⏩ `/playforce` for instant track override
- ⚡ Prefetch for near zero-gap transitions
- 🎛️ Inline controls: pause/resume, skip, loop, shuffle
- 🔐 Admin + owner authorization controls
- ♻️ Secure `/restart` command

## Commands
- `/play <query or url>`
- `/playforce <query or url>`
- `/skip`
- `/pause`
- `/resume`
- `/stop`
- `/queue`
- `/loop off|single|all`
- `/volume <10-200>`
- `/restart`

## Installation
1. Clone the repository.
2. Install dependencies:

```bash
python -m pip install -r requirements.txt
```

3. Create your environment file from the template:

```bash
cp .env.example .env
```

4. Fill `.env` values.
5. Run the bot:

```bash
python -m echopulsing.main
```

## Environment Variables
Required:
- `API_ID` - Telegram API ID
- `API_HASH` - Telegram API hash
- `BOT_TOKEN` - Telegram bot token from BotFather
- `STRING_SESSION` - Assistant account session string
- `OWNER_ID` - Bot owner Telegram user ID

Optional:
- `ADMINS` - Comma-separated Telegram user IDs
- `YTDLP_COOKIES_FILE` - Cookies file path for restricted videos
- `MONGO_URI` - MongoDB connection string
- `LOG_CHANNEL_ID` - Log channel ID
- `FFMPEG_LOCATION` - Directory containing ffmpeg binary

## Deployment
- VPS (recommended): best for stable voice playback and uptime
- Docker (optional): quick containerized deployment with `docker compose`
- Render (limited): works for bot process, but voice features can be restricted by environment/runtime constraints

## Notes
- Some videos require cookies; set `YTDLP_COOKIES_FILE` and keep the file private.
- Voice playback backend is Linux-oriented; run on Linux host, Docker Linux container, VPS, or WSL.

## Security
- Never commit `.env`, session files, bot tokens, or `cookies.txt`.
- Rotate credentials immediately if exposure is suspected.

## License
See `LICENSE`.
