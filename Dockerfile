FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    nodejs \
    && rm -rf /var/lib/apt/lists/*

ENV TEMP_DIR=/tmp/music/

COPY requirements.txt ./
RUN pip install --upgrade pip && pip install -r requirements.txt

COPY echopulsing ./echopulsing

RUN useradd -m botuser && mkdir -p /tmp/music && chown -R botuser:botuser /app /tmp/music
USER botuser

CMD ["python", "-m", "echopulsing.main"]
