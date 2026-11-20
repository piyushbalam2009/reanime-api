# ReAnime API

A Python/FastAPI wrapper for Re:ANIME's catalog and episode server-list interfaces.

The current scope is limited to anime discovery, metadata, episode lists, and server listings. Media playback and stream processing are intentionally outside this version.

## Stack

- Python 3.11+
- FastAPI
- HTTPX
- Optional Redis-compatible cache
- Vercel Python function (api/index.py)

## Local setup

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
uvicorn app.main:app --reload
```

API docs: http://127.0.0.1:8000/docs

## Environment

Configure the values in .env.example. Redis is optional; without it, the API runs without shared caching or distributed rate limiting.

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
| GET | /api/anime/{anime_id}/server?episode=1 | Compatibility server-list route |
| GET | /docs | Interactive API documentation |

## Source interfaces currently used

- GET /api/v1/search?q=...&limit=20&offset=...
- GET /api/v1/anime/{anime_id}
- GET /api/v1/anime/{anime_id}/episodes?limit=2000
- Preferred server lookup: GET /api/watch/{anime_id}/{episode_number}
- Server-list fallback/enrichment: GET /api/flix/{anilist_id}/{episode_number}

Public episode IDs use the format {anime_id}:{episode_number}, for example solo-leveling-abc:1. Upstream-style IDs such as solo-leveling-abc:ep-1 are also accepted.

Server results are returned in separate sub and dub arrays. Server-list responses contain provider listing metadata only; this API does not resolve those listings into media playback.

## Verification

Tests use mocked HTTP responses. They cover search parameters, episode normalization and sorting, server-list SUB/DUB grouping, fallback lookup, and invalid episode IDs. Mock tests do not guarantee that the live source currently accepts every endpoint.

## Deployment

- Vercel entry point: api/index.py
- VPS command: uvicorn app.main:app --host 0.0.0.0 --port 8100
- Keep environment variables and Redis credentials private.
