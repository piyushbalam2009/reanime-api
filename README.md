# ReAnime API

Standalone TypeScript/Express API for discovering anime, episodes, and ReAnime.to sub/dub server references.

- Node.js 20+
- Default port: 8080
- Host: 0.0.0.0 (set 127.0.0.1 behind a reverse proxy)
- No database required

## Playback scope

The service queries ReAnime's public JSON discovery endpoints and returns normalized anime, episode, sub, dub, and authorized server/embed references. It does not bypass DRM, decrypt protected media tokens, defeat access controls, or proxy arbitrary URLs. Some upstream servers provide embed references rather than direct HLS URLs; those are identified as embeds and are not misrepresented as direct streams.

The adapter supports the observed /api discovery routes and tries corresponding /api/v1 routes first for search, anime metadata, and episode lists. ReAnime may change its upstream API, so validate against live responses after deployment.

## Local setup

```bash
git clone https://github.com/piyushbalam2009/reanime-api.git
cd reanime-api
cp .env.example .env
npm install
npm run dev
```

Production validation:

```bash
npm run typecheck
npm run build
npm start
```

## API routes

| Method | Route | Purpose |
|---|---|---|
| GET | /api/health | Health check |
| GET | /api/search?q=...&limit=20&offset=0 | Search anime |
| GET | /api/anime/:slug | Anime metadata and episodes |
| GET | /api/episodes/:slug | Episode list |
| GET | /api/servers/:slug/:episode | All sub/dub server references |
| GET | /api/watch/:slug/:episode/sub | Sub server references |
| GET | /api/watch/:slug/:episode/dub | Dub server references |

## Example flow

Use a real slug returned by search:

```bash
curl 'http://127.0.0.1:8080/api/search?q=one%20piece'
curl 'http://127.0.0.1:8080/api/anime/one-piece-xamk74'
curl 'http://127.0.0.1:8080/api/episodes/one-piece-xamk74'
curl 'http://127.0.0.1:8080/api/servers/one-piece-xamk74/1'
curl 'http://127.0.0.1:8080/api/watch/one-piece-xamk74/1/sub'
curl 'http://127.0.0.1:8080/api/watch/one-piece-xamk74/1/dub'
```

Typical integration: search -> select returned slug -> list episodes -> retrieve servers -> choose the requested audio type -> open the returned authorized embed reference in a compatible player. If the requested audio type has no servers, show it as unavailable rather than silently switching audio.

## VPS deployment (Ubuntu, port 8080)

```bash
sudo apt update
sudo apt install -y ca-certificates curl git
curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
sudo apt install -y nodejs
sudo mkdir -p /opt/reanime-api
sudo chown "$USER":"$USER" /opt/reanime-api
git clone https://github.com/piyushbalam2009/reanime-api.git /opt/reanime-api
cd /opt/reanime-api
cp .env.example .env
npm install
npm run typecheck
npm run build
sudo cp deploy/reanime-api.service /etc/systemd/system/reanime-api.service
sudo chown -R www-data:www-data /opt/reanime-api
sudo systemctl daemon-reload
sudo systemctl enable --now reanime-api
sudo systemctl status reanime-api
curl http://127.0.0.1:8080/api/health
```

The included systemd unit binds Node to 127.0.0.1:8080 for use behind Nginx or Caddy. If exposing port 8080 directly, set HOST=0.0.0.0 in .env and configure the firewall deliberately.

## Configuration

See .env.example. Set CORS_ORIGINS to comma-separated trusted origins in production instead of *. REQUEST_TIMEOUT_MS controls upstream timeout; RATE_LIMIT_WINDOW_MS and RATE_LIMIT_MAX configure the per-process request limit.

## Reliability and security

- Sub and dub are normalized independently.
- Embed links are restricted to HTTPS ReAnime and FlixCloud hostnames.
- The API never fetches user-supplied URLs, preventing use as an open proxy.
- Do not cache expiring embed references or signed playback URLs.
- Upstream errors are returned as structured JSON.
- No user credentials or watch history are stored.
- Respect the source site's terms, access rules, and rate limits.

## CI

GitHub Actions runs TypeScript typechecking and a production build on pushes and pull requests to main.