import ipaddress
import re
from urllib.parse import urljoin, urlsplit, urlencode

import httpx
from fastapi import HTTPException, Query, Request
from fastapi.responses import PlainTextResponse, StreamingResponse

from app.config import settings

_URI_ATTRIBUTE = re.compile(r'URI="([^"]+)"', re.IGNORECASE)
_TIMEOUT = httpx.Timeout(45.0, connect=10.0)
_MAX_PLAYLIST_BYTES = 2 * 1024 * 1024
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
        # Expose only the hostname (never the signed URL, path, or query) to help
        # operators configure the narrow provider/CDN allowlist safely.
        raise HTTPException(
            status_code=403,
            detail={"error": "upstream_host_not_allowlisted", "host": host},
        )
    return value


def proxy_url_for(request: Request, target: str) -> str:
    """Build this API's proxy URL for an allowlisted upstream resource."""
    validate_upstream_url(target)
    endpoint = request.url.replace(path="/api/proxy/hls", query="")
    return f"{endpoint}?{urlencode({'url': target})}"


def _payload_kind(payload: bytes) -> str:
    """Return a safe diagnostic classification without exposing response contents."""
    sample = payload.lstrip(b"\xef\xbb\xbf \t\r\n").lower()
    if sample.startswith(b"#extm3u"):
        return "hls"
    if sample.startswith((b"<!doctype html", b"<html", b"<head", b"<body")):
        return "html"
    if sample.startswith((b"{", b"[")):
        return "json"
    try:
        payload.decode("utf-8")
    except UnicodeDecodeError:
        return "binary_or_encoded"
    return "text"


def _decode_playlist(payload: bytes, content_type: str) -> str:
    """Reject mislabeled/opaque payloads instead of rewriting them as playlists."""
    try:
        body = payload.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise HTTPException(
            status_code=502,
            detail={
                "error": "upstream_not_hls",
                "message": "The upstream response was not a UTF-8 HLS playlist.",
                "upstream_content_type": content_type[:120],
                "upstream_bytes": len(payload),
                "upstream_body_kind": _payload_kind(payload),
            },
        ) from exc

    if not body.lstrip(" \t\r\n").startswith("#EXTM3U"):
        raise HTTPException(
            status_code=502,
            detail={
                "error": "upstream_not_hls",
                "message": "The upstream labeled this response as a playlist, but its body did not begin with #EXTM3U.",
                "upstream_content_type": content_type[:120],
                "upstream_bytes": len(payload),
                "upstream_body_kind": _payload_kind(payload),
            },
        )
    return body


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
                    lambda match: f'URI="{proxy_url_for(request, urljoin(base_url, match.group(1)))}"',
                    raw_line,
                )
            )
        else:
            output.append(proxy_url_for(request, urljoin(base_url, line)))
    return "\n".join(output) + ("\n" if body.endswith(("\n", "\r")) else "")


async def hls_proxy(
    request: Request,
    url: str = Query(..., min_length=12, max_length=4096),
):
    """Proxy one HLS resource; validate and rewrite playlists before returning them."""
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
            payload = bytearray()
            async for chunk in upstream.aiter_bytes():
                payload.extend(chunk)
                if len(payload) > _MAX_PLAYLIST_BYTES:
                    raise HTTPException(
                        status_code=502,
                        detail={
                            "error": "upstream_playlist_too_large",
                            "message": "The upstream playlist exceeded the configured size limit.",
                            "max_bytes": _MAX_PLAYLIST_BYTES,
                        },
                    )
            raw_payload = bytes(payload)

            try:
                body = _decode_playlist(raw_payload, content_type)
            except HTTPException as exc:
                if isinstance(exc.detail, dict) and exc.detail.get("error") == "upstream_not_hls":
                    preview = raw_payload.decode("utf-8", errors="replace")
                    preview = re.sub(r"https?://[^\\s\"'<>]+", "[URL REDACTED]", preview)
                    preview = re.sub(
                        r"(?i)(token|authorization|cookie|signature|key|secret)(\\s*[:=]\\s*)[^,\\s\"'<>]+",
                        r"\\1\\2[REDACTED]",
                        preview,
                    )
                    preview = re.sub(r"[A-Za-z0-9_-]{40,}", "[LONG VALUE REDACTED]", preview)

                    print(
                        "[HLS UPSTREAM DIAGNOSTIC]",
                        {
                            "status": upstream.status_code,
                            "content_type": content_type,
                            "bytes": len(raw_payload),
                            "preview": preview[:500],
                        },
                        flush=True,
                    )
                raise
            rewritten = rewrite_playlist(body, target, request)
        except HTTPException:
            raise
        except (httpx.HTTPError, UnicodeDecodeError) as exc:
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
