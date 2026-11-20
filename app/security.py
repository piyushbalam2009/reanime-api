import base64
import hashlib
import hmac
import json
import time
from urllib.parse import urlsplit

from app.main import settings

class ProxyTokenError(ValueError):
    pass

def validate_source_url(url: str) -> str:
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise ProxyTokenError("Only HTTPS source URLs are allowed")
    if parsed.username or parsed.password:
        raise ProxyTokenError("URL credentials are not allowed")
    if parsed.hostname.lower().rstrip(".") not in settings.allowed_hosts:
        raise ProxyTokenError("Source host is not allowlisted")
    return url

def _enc(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()

def _dec(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))

def create_proxy_token(url: str, expires_at: int | None = None) -> str:
    secret = settings.proxy_signing_secret
    if not secret:
        raise ProxyTokenError("PROXY_SIGNING_SECRET is not configured")
    validate_source_url(url)
    payload = {
        "url": url,
        "exp": expires_at or int(time.time()) + settings.proxy_token_ttl_seconds,
        "aud": "reanime-hls-proxy",
    }
    body = _enc(json.dumps(payload, separators=(",", ":")).encode())
    sig = hmac.new(secret.encode(), body.encode(), hashlib.sha256).digest()
    return body + "." + _enc(sig)

def verify_proxy_token(token: str) -> dict:
    secret = settings.proxy_signing_secret
    if not secret:
        raise ProxyTokenError("Proxy signing is not configured")
    try:
        body, signature = token.split(".", 1)
        expected = hmac.new(secret.encode(), body.encode(), hashlib.sha256).digest()
        if not hmac.compare_digest(expected, _dec(signature)):
            raise ProxyTokenError("Invalid token signature")
        payload = json.loads(_dec(body))
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        if isinstance(exc, ProxyTokenError):
            raise
        raise ProxyTokenError("Malformed proxy token") from exc
    if payload.get("aud") != "reanime-hls-proxy":
        raise ProxyTokenError("Invalid token audience")
    if not isinstance(payload.get("exp"), int) or payload["exp"] < int(time.time()):
        raise ProxyTokenError("Token expired")
    validate_source_url(payload.get("url", ""))
    return payload
