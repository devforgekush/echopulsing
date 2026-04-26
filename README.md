# EchoPulsing Music

EchoPulsing Music is a Telegram music streaming bot for group voice chats. It is built with Pyrogram, PyTgCalls, MongoDB, yt-dlp, and FFmpeg, and it is designed for fast song start-up, inline controls, and clean queue management.

## Features
- `/play`, `/pause`, `/resume`, `/skip`, `/stop`, `/end`, `/queue`, `/current`
- YouTube search and direct URL playback
- Per-group queue, loop mode, and volume control
- Now Playing cards with progress refresh and loading feedback
- Optional cookies support for restricted videos
- Automatic cleanup of temporary media files
- Long polling only, no webhooks

## Setup
1. Copy the example environment file:
	```bash
	cp .env.example .env
	```
2. Install dependencies:
	```bash
	python -m pip install -r requirements.txt
	```
3. Fill in the required values in `.env`.
4. Start the bot:
	```bash
	python -m echopulsing.main
	```

## Environment Variables
Required:
- `API_ID` - Telegram API ID
- `API_HASH` - Telegram API hash
- `BOT_TOKEN` - Bot token from BotFather
- `STRING_SESSION` - User session string for voice chat playback
- `MONGO_URI` - MongoDB connection string

Optional:
- `LOG_CHANNEL_ID` - Log channel for bot events
- `DOWNLOAD_CONCURRENCY` - Number of parallel yt-dlp jobs
- `TEMP_DIR` - Temporary download directory
- `YTDLP_COOKIES_FILE` - Path to a cookies.txt file
- `FFMPEG_LOCATION` - Custom FFmpeg binary path

## Docker Deployment
```bash
docker compose up -d --build
```

The Docker setup uses `.env` for secrets and `restart: always` so the bot comes back after host restarts.

## VPS Deployment
1. Install Docker and Docker Compose on your VPS.
2. Clone this repository.
3. Create `.env` from `.env.example` and add your real credentials.
4. Run `docker compose up -d --build`.
5. Check logs with `docker compose logs -f bot`.

## Notes
- The bot does not use the YouTube Data API.
- If `cookies.txt` exists in the project root, yt-dlp will use it automatically.
- Keep `.env`, `cookies.txt`, and session strings private.
