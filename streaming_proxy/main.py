import re
from urllib.parse import urljoin

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import Response, StreamingResponse

from app.main import settings
from app.security import ProxyTokenError, create_proxy_token, validate_source_url, verify_proxy_token

app = FastAPI(title="ReAnime Streaming Proxy", docs_url=None, redoc_url=None)
MAX_PLAYLIST_BYTES = 2 * 1024 * 1024
URI_ATTR = re.compile(r'URI="([^"]+)"')

def rewrite_playlist(text: str, base_url: str, expires_at: int) -> str:
    if not settings.stream_proxy_base_url:
        raise ProxyTokenError("STREAM_PROXY_BASE_URL is not configured")
    def proxy(uri: str) -> str:
        absolute = urljoin(base_url, uri)
        validate_source_url(absolute)
        token = create_proxy_token(absolute, expires_at)
        return f"{settings.stream_proxy_base_url.rstrip('/')}/proxy/{token}"
    result = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            result.append(line)
        elif stripped.startswith("#"):
            result.append(URI_ATTR.sub(lambda match: f'URI="{proxy(match.group(1))}"', line))
        else:
            result.append(proxy(stripped))
    return "\n".join(result) + "\n"

@app.get("/health")
async def health():
    return {"status": "ok", "service": "streaming-proxy"}

@app.api_route("/proxy/{token}", methods=["GET", "HEAD"])
async def proxy_resource(token: str, request: Request):
    try:
        claims = verify_proxy_token(token)
        source_url = claims["url"]
    except (ProxyTokenError, KeyError) as exc:
        raise HTTPException(status_code=403, detail="Invalid or expired proxy token") from exc

    headers = {}
    for name in ("range", "if-range"):
        if request.headers.get(name):
            headers[name.title()] = request.headers[name]

    client = httpx.AsyncClient(timeout=httpx.Timeout(60, connect=10), follow_redirects=False)
    upstream = await client.send(client.build_request(request.method, source_url, headers=headers), stream=True)

    if upstream.is_redirect:
        location = upstream.headers.get("location")
        await upstream.aclose()
        await client.aclose()
        if not location:
            raise HTTPException(status_code=502, detail="Invalid upstream redirect")
        try:
            destination = validate_source_url(urljoin(source_url, location))
            next_token = create_proxy_token(destination, claims["exp"])
        except ProxyTokenError as exc:
            raise HTTPException(status_code=502, detail="Redirect host is not allowed") from exc
        return Response(status_code=307, headers={"Location": f"{settings.stream_proxy_base_url.rstrip('/')}/proxy/{next_token}"})

    content_type = upstream.headers.get("content-type", "application/octet-stream")
    if request.method == "HEAD":
        response_headers = {
            key: upstream.headers[key]
            for key in ("content-length", "content-range", "accept-ranges", "etag", "last-modified")
            if key in upstream.headers
        }
        await upstream.aclose()
        await client.aclose()
        return Response(status_code=upstream.status_code, media_type=content_type, headers=response_headers)
    is_playlist = source_url.lower().split("?", 1)[0].endswith(".m3u8") or "mpegurl" in content_type.lower()
    if is_playlist:
        body = await upstream.aread()
        await upstream.aclose()
        await client.aclose()
        if len(body) > MAX_PLAYLIST_BYTES:
            raise HTTPException(status_code=413, detail="Playlist too large")
        try:
            text = rewrite_playlist(body.decode("utf-8-sig"), source_url, claims["exp"])
        except (UnicodeDecodeError, ProxyTokenError) as exc:
            raise HTTPException(status_code=502, detail="Unable to safely rewrite playlist") from exc
        return Response(text, status_code=upstream.status_code, media_type="application/vnd.apple.mpegurl", headers={"Cache-Control": "private, no-store"})

    forwarded = {}
    for name in ("content-length", "content-range", "accept-ranges", "etag", "last-modified"):
        if name in upstream.headers:
            forwarded[name] = upstream.headers[name]
    forwarded["Cache-Control"] = "private, no-store"

    async def stream_body():
        try:
            async for chunk in upstream.aiter_bytes():
                yield chunk
        finally:
            await upstream.aclose()
            await client.aclose()

    return StreamingResponse(stream_body(), status_code=upstream.status_code, media_type=content_type, headers=forwarded)
