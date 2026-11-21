# ReAnime API

A Python/FastAPI wrapper for Re:ANIME's catalog and episode server-list interfaces, with FlixCloud playback resolution.

## Requirements

- Python 3.11+
- Node.js 20+ on PATH (required for the FlixCloud WASM resolver)
- FastAPI, HTTPX, optional Redis

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
uvicorn app.main:app --reload
```

## API routes

| Method | Route | Purpose |
|---|---|---|
| GET | / | API information |
| GET | /health | Health check |
| GET | /api/status | API and source status |
| GET | /api/search?q=...&page=1 | Search anime |
| GET | /api/anime/{anime_id} | Anime details |
| GET | /api/anime/{anime_id}/episodes | Episode list |
| GET | /api/episode/{anime_id}:{episode}/servers | Episode server listings |
| GET | /api/episode/{anime_id}:{episode}/resolve?audio=sub&server=HD-2 | Resolve selected FlixCloud embed to HLS + subtitles |
| GET | /api/anime/{anime_id}/server?episode=1 | Compatibility server-list route |

## FlixCloud playback

Each resolve request fetches a fresh embed page, reads its current SSR player data and WASM payload, obtains its one-time playback token, and resolves the HLS URL and subtitle tracks. The token metadata request is performed exactly once. A 410 response is surfaced without retrying. Do not cache tokens or resolved stream URLs.

The response includes playback.url, playback.subtitles, playback.thumbnails_vtt, video title, and intro/outro chapter metadata. Node.js 20+ must be available to the PM2 process. This resolver returns the provider's HLS URL directly; it does not implement an HLS proxy.

## Verification

```bash
PYTHONPATH=. pytest -q
python -m compileall -q app api tests
node --check app/resolvers/flixcloud_decrypt.mjs
```

## Deployment

- VPS command: uvicorn app.main:app --host 0.0.0.0 --port 8100
- Keep environment variables and Redis credentials private.
