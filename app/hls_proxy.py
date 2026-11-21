import ipaddress
import re
from urllib.parse import urljoin, urlsplit, urlencode

import httpx
from fastapi import HTTPException, Query, Request
from fastapi.responses import PlainTextResponse, StreamingResponse

from app.config import settings

_URI_ATTRIBUTE = re.compile(r'URI="([^"]+)"', re.IGNORECASE)
_TIMEOUT = httpx.Timeout(45.0, connect=10.0)
_FORWARD_REQUEST_HEADERS = {"range", "if-range"}
_FORWARD_RESPONSE_HEADERS = {
    "accept-ranges", "cache-control", "content-length", "content-encoding",
    "content-range", "content-type", "etag", "last-modified",
}


def validate_upstream_url(value: str) -> str:
    """Allow HTTPS URLs only on explicitly configured provider/CDN hostnames."""
    try:
        parsed = urlsplit(value)
        host = (parsed.hostname or "").lower().rstrip(".")
        port = parsed.port
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid upstream URL.") from exc

    if (
        parsed.scheme != "https"
        or not host
        or parsed.username is not None
        or parsed.password is not None
        or port not in (None, 443)
        or parsed.fragment
    ):
        raise HTTPException(status_code=400, detail="Only HTTPS provider URLs are allowed.")

    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        raise HTTPException(status_code=400, detail="IP-address upstreams are not allowed.")

    allowed = [item.strip().lower().lstrip(".") for item in settings.hls_proxy_allowed_hosts.split(",") if item.strip()]
    if not any(host == domain or host.endswith("." + domain) for domain in allowed):
        raise HTTPException(status_code=403, detail="Upstream host is not allowlisted.")
    return value


def _proxy_uri(request: Request, target: str) -> str:
    validate_upstream_url(target)
    return f"{request.url_for('hls_proxy')}?{urlencode({'url': target})}"


def rewrite_playlist(body: str, base_url: str, request: Request) -> str:
    """Rewrite both URI lines and URI= attributes in an HLS playlist."""
    output: list[str] = []
    for raw_line in body.splitlines():
        line = raw_line.strip()
        if not line:
            output.append(raw_line)
        elif line.startswith("#"):
            output.append(
                _URI_ATTRIBUTE.sub(
                    lambda match: f'URI="{_proxy_uri(request, urljoin(base_url, match.group(1)))}"',
                    raw_line,
                )
            )
        else:
            output.append(_proxy_uri(request, urljoin(base_url, line)))
    return "\n".join(output) + ("\n" if body.endswith(("\n", "\r")) else "")


async def hls_proxy(
    request: Request,
    url: str = Query(..., min_length=12, max_length=4096),
):
    """Proxy one HLS resource; playlists are rewritten to proxy every child URI."""
    target = validate_upstream_url(url)
    headers = {
        "User-Agent": request.headers.get(
            "user-agent",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36",
        ),
        "Accept": "*/*",
        "Referer": "https://flixcloud.cc/",
        "Origin": "https://flixcloud.cc",
    }
    for name in _FORWARD_REQUEST_HEADERS:
        if name in request.headers:
            headers[name] = request.headers[name]

    client = httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=False)
    try:
        upstream = await client.send(client.build_request("GET", target, headers=headers), stream=True)
    except httpx.HTTPError as exc:
        await client.aclose()
        raise HTTPException(status_code=502, detail="Unable to fetch the upstream HLS resource.") from exc

    if 300 <= upstream.status_code < 400:
        await upstream.aclose()
        await client.aclose()
        raise HTTPException(status_code=502, detail="Upstream redirects are not followed by the HLS proxy.")
    if upstream.status_code >= 400:
        status = upstream.status_code
        await upstream.aclose()
        await client.aclose()
        raise HTTPException(status_code=status, detail="The upstream HLS resource returned an error.")

    content_type = upstream.headers.get("content-type", "")
    is_playlist = urlsplit(target).path.lower().endswith(".m3u8") or "mpegurl" in content_type.lower()

    if is_playlist:
        try:
            payload = (await upstream.aread()).decode("utf-8-sig")
            rewritten = rewrite_playlist(payload, target, request)
        except HTTPException:
            raise
        except (UnicodeDecodeError, httpx.HTTPError) as exc:
            raise HTTPException(status_code=502, detail="The upstream playlist could not be read.") from exc
        finally:
            await upstream.aclose()
            await client.aclose()
        return PlainTextResponse(
            rewritten,
            media_type="application/vnd.apple.mpegurl",
            headers={"Cache-Control": "no-store"},
        )

    response_headers = {
        name: value
        for name, value in upstream.headers.items()
        if name.lower() in _FORWARD_RESPONSE_HEADERS
    }

    async def stream_body():
        try:
            async for chunk in upstream.aiter_raw():
                yield chunk
        finally:
            await upstream.aclose()
            await client.aclose()

    return StreamingResponse(
        stream_body(),
        status_code=upstream.status_code,
        headers=response_headers,
    )
