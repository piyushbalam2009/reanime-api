# ReAnime API

Standalone TypeScript API for discovering anime, episodes, and ReAnime.to sub/dub server references.

## Scope and playback limitation

This service uses ReAnime's publicly exposed JSON discovery endpoints and returns the server/embed references supplied by the site. It does **not** defeat DRM, decrypt protected media tokens, bypass access controls, or proxy arbitrary URLs. Where the upstream returns a direct, public HLS URL, a future adapter can validate and expose it; otherwise use the supplied authorized embed link in a compatible player.

ReAnime and its playback hosts can change their APIs at any time. Treat upstream response fields as untrusted and expect occasional adapter maintenance.

## Requirements

- Ubuntu VPS
- Node.js 20 or newer
- npm

## Local setup

```bash
git clone https://github.com/piyushbalam2009/reanime-api.git
cd reanime-api
cp .env.example .env
npm install
npm run dev
```

Default port: **8080**.

## API

| Method | Route | Purpose |
|---|---|---|
| GET | `/` | Service overview |
| GET | `/api/health` | Health check |
| GET | `/api/search?q=...` | Search ReAnime |
| GET | `/api/anime/:slug` | Anime metadata and episodes |
| GET | `/api/episodes/:slug` | Episode list |
| GET | `/api/servers/:slug/:episode` | All discovered sub/dub server references |
| GET | `/api/watch/:slug/:episode/sub` | Sub server references |
| GET | `/api/watch/:slug/:episode/dub` | Dub server references |

### Example flow

```bash
curl 'http://localhost:8080/api/search?q=one%20piece'
curl 'http://localhost:8080/api/anime/one-piece-example'
curl 'http://localhost:8080/api/servers/one-piece-example/1'
curl 'http://localhost:8080/api/watch/one-piece-example/1/dub'
```

Use a real slug returned by search; the example slug above is illustrative.

## Ubuntu VPS deployment

```bash
sudo apt update
sudo apt install -y ca-certificates curl git
curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
sudo apt install -y nodejs
git clone https://github.com/piyushbalam2009/reanime-api.git
cd reanime-api
cp .env.example .env
npm ci
npm run build
```

Run with systemd (recommended). Create `/etc/systemd/system/reanime-api.service`:

```ini
[Unit]
Description=ReAnime API
After=network.target

[Service]
Type=simple
WorkingDirectory=/opt/reanime-api
ExecStart=/usr/bin/node /opt/reanime-api/dist/server.js
Restart=always
RestartSec=5
Environment=NODE_ENV=production
Environment=PORT=8080
Environment=HOST=127.0.0.1
User=www-data

[Install]
WantedBy=multi-user.target
```

Adjust `WorkingDirectory` and permissions to your actual deployment directory. Then:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now reanime-api
sudo systemctl status reanime-api
curl http://127.0.0.1:8080/api/health
```

For public access, put Nginx or Caddy in front and use HTTPS. Keep the Node process bound to localhost when using a reverse proxy.

## Environment variables

See `.env.example`. Set `CORS_ORIGINS` to comma-separated trusted origins instead of `*` when deploying for real users.

## Development checks

```bash
npm run typecheck
npm run build
```

## Notes

- Stream/embed references and signed playback URLs may expire; do not cache them.
- Sub and dub are queried separately and can be absent independently.
- This API does not store user data or require a database.
- Respect ReAnime's terms, robots/access rules, and upstream rate limits.
