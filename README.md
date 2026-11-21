# ReAnime API

A Python/FastAPI wrapper for Re:ANIME's catalog and episode server-list interfaces, with FlixCloud playback resolution.

## Requirements

- Python 3.11+
- Node.js 20+ on PATH (required for the FlixCloud WASM resolver)
- Docker Engine (for the optional FlareSolverr browser service)
- FastAPI, HTTPX, optional Redis

## Setup

    python -m venv .venv
    source .venv/bin/activate
    pip install -r requirements.txt
    cp .env.example .env
    uvicorn app.main:app --reload

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
| GET | /api/episode/{episode_id}/resolve?audio=sub&server=HD-2 | Resolve selected FlixCloud embed to HLS + subtitles |
| GET | /api/anime/{anime_id}/server?episode=1 | Compatibility server-list route |

## FlixCloud playback

FlixCloud embed and one-time token requests use a dedicated FlareSolverr browser session. The embed page and token metadata request share the same session; the session is destroyed after each resolution. The one-time token request is made exactly once and is never retried. The existing WASM/AES decryption then extracts the HLS URL and subtitle tracks.

Run FlareSolverr locally and do not expose its port publicly:

    docker run -d \
      --name flaresolverr \
      --restart unless-stopped \
      -p 127.0.0.1:8191:8191 \
      -e LOG_LEVEL=info \
      ghcr.io/flaresolverr/flaresolverr:latest

Set these values in .env:

    FLIXCLOUD_FLARESOLVERR_URL=http://127.0.0.1:8191
    FLIXCLOUD_FLARESOLVERR_TIMEOUT_SECONDS=60000

Node.js 20+ must be available to the PM2 process. Do not cache one-time tokens or resolved stream URLs. This resolver returns the provider's HLS URL directly; it does not implement an HLS proxy.

## Verification

    PYTHONPATH=. pytest -q
    python -m compileall -q app api tests
    node --check app/resolvers/flixcloud_decrypt.mjs

## Deployment

- VPS command: uvicorn app.main:app --host 0.0.0.0 --port 8100
- Keep FlareSolverr bound to localhost.
- Keep environment variables and Redis credentials private.
