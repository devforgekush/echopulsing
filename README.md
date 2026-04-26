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
- `YTDLP_COOKIES_FILE` - Path to your `cookies.txt` file (not cookie text itself)
- `MONGO_URI` - MongoDB connection string
- `LOG_CHANNEL_ID` - Log channel ID
- `FFMPEG_LOCATION` - Directory containing ffmpeg binary

## yt-dlp Cookies Setup (Important)

Some YouTube videos are age-restricted/private and require login cookies.

What to set:
- `YTDLP_COOKIES_FILE` must contain a file path.
- The file should usually be named `cookies.txt`.
- The file content must be Netscape cookies format (exported from your browser).

What not to set:
- Do not paste raw cookie text directly into `YTDLP_COOKIES_FILE`.
- Do not paste JSON/browser storage output in `.env`.

Example `.env` (Windows):

```env
YTDLP_COOKIES_FILE=C:/Users/yourname/Desktop/telegram music bot/cookies.txt
```

Example `.env` (Linux/VPS):

```env
YTDLP_COOKIES_FILE=/home/yourname/telegram-music-bot/cookies.txt
```

Auto-detect behavior:
- If `YTDLP_COOKIES_FILE` is not set, the bot will automatically use `cookies.txt` from the project root (if present).

Quick checklist:
1. Export YouTube cookies to `cookies.txt`.
2. Place the file in project root or set full path in `YTDLP_COOKIES_FILE`.
3. Keep `cookies.txt` private and never commit it.
4. Refresh/export again if cookies expire.

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
