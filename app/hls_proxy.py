import base64
import binascii
import ipaddress
import logging
import re
import time
from urllib.parse import urljoin, urlsplit, urlencode

import httpx
from fastapi import HTTPException, Query, Request
from fastapi.responses import PlainTextResponse, StreamingResponse

from app.config import settings

logger = logging.getLogger("reanime.hls")

_URI_ATTRIBUTE = re.compile(r'URI="([^"]+)"', re.IGNORECASE)
_TIMEOUT = httpx.Timeout(45.0, connect=10.0)
_MAX_PLAYLIST_BYTES = 2 * 1024 * 1024

_PLAYLIST_KEY_TTL_SECONDS = 30 * 60
_MAX_CACHED_PLAYLIST_KEYS = 256
_PLAYLIST_KEYS: dict[str, tuple[float, bytes]] = {}


def store_playlist_key(video_id: str, encoded_key: str | None) -> bool:
    """Keep a FlixCloud playlist key in process memory; never expose it in URLs."""
    if not isinstance(video_id, str) or not re.fullmatch(r"[A-Za-z0-9-]{8,128}", video_id):
        return False
    if not isinstance(encoded_key, str) or len(encoded_key) > 128:
        return False
    try:
        key = base64.b64decode(encoded_key, validate=True)
    except (binascii.Error, ValueError):
        return False
    if len(key) != 32:
        return False

    now = time.monotonic()
    expired = [item for item, (expires, _) in _PLAYLIST_KEYS.items() if expires <= now]
    for item in expired:
        _PLAYLIST_KEYS.pop(item, None)
    if len(_PLAYLIST_KEYS) >= _MAX_CACHED_PLAYLIST_KEYS and video_id not in _PLAYLIST_KEYS:
        oldest = min(_PLAYLIST_KEYS, key=lambda item: _PLAYLIST_KEYS[item][0])
        _PLAYLIST_KEYS.pop(oldest, None)
    _PLAYLIST_KEYS[video_id] = (now + _PLAYLIST_KEY_TTL_SECONDS, key)
    return True


def _playlist_key_for_url(target: str) -> bytes | None:
    path = urlsplit(target).path
    match = re.search(r"/_v\d+/([A-Za-z0-9-]{8,128})/", path)
    if not match:
        return None
    entry = _PLAYLIST_KEYS.get(match.group(1))
    if not entry:
        return None
    expires, key = entry
    if expires <= time.monotonic():
        _PLAYLIST_KEYS.pop(match.group(1), None)
        return None
    return key


def _decode_flixcloud_playlist(payload: bytes, key: bytes) -> str | None:
    """Decode the provider playlist envelope using its Base64/XOR format."""
    try:
        encoded = payload.decode("utf-8-sig").strip()
        if not encoded or len(encoded) > (_MAX_PLAYLIST_BYTES * 2):
            return None
        encrypted = base64.b64decode(encoded, validate=True)
        if not encrypted:
            return None
        decoded = bytes(value ^ key[index % len(key)] for index, value in enumerate(encrypted))
        body = decoded.decode("utf-8-sig")
        return body if body.startswith("#EXTM3U") else None
    except (UnicodeDecodeError, binascii.Error, ValueError):
        return None


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


def _decode_playlist(payload: bytes, content_type: str, playlist_key: bytes | None = None) -> str:
    """Reject mislabeled/opaque payloads instead of rewriting them as playlists."""
    try:
        body = payload.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        decoded_flix = _decode_flixcloud_playlist(payload, playlist_key) if playlist_key else None
        if decoded_flix is not None:
            return decoded_flix
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
        # Accept Base64 only when it decodes into a recognizable HLS manifest.
        # FlixCloud XOR decoding is attempted below only when its server-side key exists.
        compact = "".join(body.split())
        if compact and len(compact) <= (_MAX_PLAYLIST_BYTES * 2):
            try:
                decoded = base64.b64decode(compact, validate=True).decode("utf-8-sig")
            except (binascii.Error, UnicodeDecodeError, ValueError):
                decoded = ""
            if decoded.lstrip(" \t\r\n").startswith("#EXTM3U"):
                return decoded

        if playlist_key:
            decoded_flix = _decode_flixcloud_playlist(payload, playlist_key)
            if decoded_flix is not None:
                return decoded_flix

        raise HTTPException(
            status_code=502,
            detail={
                "error": "upstream_not_hls",
                "message": "The upstream response was not a recognizable HLS playlist.",
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
            playlist_key = _playlist_key_for_url(target)
            logger.info(
                "FlixCloud playlist decode diagnostic: video_id_match=%s key_found=%s key_length=%s payload_bytes=%s",
                bool(re.search(r"/_v\\d+/[A-Za-z0-9-]{8,128}/", urlsplit(target).path)),
                playlist_key is not None,
                len(playlist_key) if playlist_key else 0,
                len(raw_payload),
            )

            try:
                body = _decode_playlist(raw_payload, content_type, playlist_key)
            except HTTPException as exc:
                if isinstance(exc.detail, dict) and exc.detail.get("error") == "upstream_not_hls":
                    preview = raw_payload.decode("utf-8", errors="replace")
                    # Never log URLs, query strings, cookies, or long opaque values.
                    preview = re.sub(r"https?://[^\s\"'<>]+", "[URL REDACTED]", preview)
                    preview = re.sub(
                        r"(?i)(token|authorization|cookie|signature|key|secret)(\s*[:=]\s*)[^,\s\"'<>]+",
                        r"\1\2[REDACTED]",
                        preview,
                    )
                    preview = re.sub(r"[A-Za-z0-9_-]{24,}", "[OPAQUE VALUE REDACTED]", preview)

                    print(
                        "[HLS UPSTREAM DIAGNOSTIC]",
                        {
                            "status": upstream.status_code,
                            "content_type": content_type[:120],
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
