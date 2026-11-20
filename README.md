# ReAnime API

Python/FastAPI public API targeting the permitted public interfaces of [Re:ANIME](https://reanime.cz/).

**Status:** initial foundation. Source-specific routes intentionally return `501 source_not_configured` until the site's real public interfaces and authorized integration method are verified. No fake data or invented playback URLs are returned.

## Stack
- FastAPI / Python 3.11+
- Vercel Python function (`api/index.py`)
- Redis-compatible cache (Upstash recommended)
- Separate FastAPI HLS proxy for VPS deployment
- HMAC-signed, short-lived proxy tokens and exact upstream host allowlist

## Local setup
```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
uvicorn app.main:app --reload
```
Docs: http://127.0.0.1:8000/docs

## Environment
Configure values from `.env.example`. Generate `PROXY_SIGNING_SECRET` with:
```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
``

## Deployment
- Connect the repo to Vercel; entry point is `api/index.py`.
- Deploy `streaming_proxy.main:app` separately on a VPS suited to sustained media delivery.
- Configure the same signing secret and a narrow `SOURCE_ALLOWED_HOSTS` allowlist on both services.
- Keep Redis and any browser-session service private.

## Routes
- `GET /`, `GET /health`, `GET /api/status`
- `GET /api/search?q=...`
- `GET /api/anime/{id}`
- `GET /api/anime/{id}/episodes`
- `GET /api/episode/{id}/servers`
- Compatibility alias: `GET /api/anime/{anime_id}/server?episode=1`
- `GET /api/watch/{episode_id}/{sub|dub}`
- `GET /docs`

Catalog and playback routes return a clear 501 until source interfaces are verified.

## Cloudflare / FlareSolverr
A FlareSolverr URL may be configured for a site-operator-approved integration, but this initial implementation does not invoke it or automate challenges. Do not use browser automation to evade access restrictions. Prefer an operator-provided API, integration, or IP allowlist.

## Security
The streaming proxy accepts signed tokens only; it is not an arbitrary URL proxy. It validates HTTPS and exact allowlisted hosts, does not automatically follow redirects, and rewrites HLS references to signed proxy URLs. Respect the source operator's permissions, rate limits, and terms.


## Re:ANIME source adapter

The source adapter now calls the public Re:ANIME JSON interfaces:

- `GET /api/v1/search?q=...&limit=20&offset=...`
- `GET /api/v1/anime/{anime_id}`
- `GET /api/v1/anime/{anime_id}/episodes?limit=2000`
- `GET /api/watch/{anime_id}/{episode_number}`
- Optional server-list enrichment: `GET /api/flix/{anilist_id}/{episode_number}`

Our public episode IDs use `{anime_id}:{episode_number}`, for example
`solo-leveling-abc:1`. The original upstream episode identifier (when present,
such as `ep-1`) is preserved separately as `source_episode_id`; it is not
substituted for the anime slug and episode number used by the server lookup.
Server results are separated into `sub` and `dub`. The adapter tries the
legacy `/api/watch/{slug}/{episode}` path first and falls back to
`/api/v1/watch/{slug}/{episode}` only when the legacy path returns 404.

### Playback boundary

If the upstream response explicitly contains a direct HLS URL, the API can pass
it to the configured proxy (subject to the proxy host allowlist). If Re:ANIME
only returns an embed player URL, the watch endpoint returns
`playback.type = "embed"`. It does not claim that an embed is an M3U8 stream,
and it does not reverse-engineer or defeat third-party player encryption.

### Verification status

Adapter tests use mocked HTTP responses. They verify parsing, pagination
parameters, SUB/DUB grouping, embed fallback, and invalid-ID rejection. They
do not prove that the live site currently accepts every endpoint or that an
embed produces playable media. Confirm live behavior with a permitted test
title before deploying publicly.
