# EchoPulsing Music

EchoPulsing Music is a Telegram group voice-chat music bot with low-latency URL streaming.
It uses yt-dlp direct stream extraction, FFmpeg, PyTgCalls, and MongoDB playlists.

## Features
- Fast streaming pipeline: yt-dlp URL extraction -> FFmpeg -> PyTgCalls
- Per-chat async queue with race-safe playback locks
- Inline controls with callback actions:
  - play/pause toggle
  - skip
  - loop modes (`off`, `single`, `all`)
  - shuffle
- Prefetch (zero-gap playback): resolves next track while current track is playing
- Queue commands and playlist persistence
- Cookie fallback for restricted media (`YTDLP_COOKIES_FILE`)

## Requirements
- Python 3.11+
- FFmpeg available in PATH (or set `FFMPEG_LOCATION`)
- MongoDB
- Telegram API credentials and a user string session

## Setup
1. Clone repository.
2. Create env file from example:

```bash
cp .env.example .env
```

3. Install dependencies:

```bash
python -m pip install -r requirements.txt
```

4. Fill `.env` values.
5. Run:

```bash
python -m echopulsing.main
```

## Environment Variables
Required:
- `API_ID`
- `API_HASH`
- `BOT_TOKEN`
- `STRING_SESSION`
- `MONGO_URI`

Optional:
- `LOG_CHANNEL_ID`
- `YTDLP_COOKIES_FILE` (path to cookies file)
- `FFMPEG_LOCATION` (directory containing ffmpeg binary)

## Commands
- `/play <query or url>`
- `/pause`
- `/resume`
- `/skip`
- `/stop`
- `/queue`
- `/current`
- `/loop off|single|all`
- `/volume <10-200>`
- `/playlist_save <name>`
- `/playlist_load <name>`

## Docker

```bash
docker compose up -d --build
```

## Deployment Options
- VPS (Docker Compose)
- Render (Docker service)

## Security Notes
- Never commit `.env`, session files, or `cookies.txt`.
- Keep `STRING_SESSION` private.
- Rotate credentials if leaked.

## Architecture
- `echopulsing/services`: runtime, voice, queue, yt-dlp, db
- `echopulsing/handlers`: bot command and callback handlers
- `echopulsing/utils`: UI, logger, helper utilities

## License
See `LICENSE`.
