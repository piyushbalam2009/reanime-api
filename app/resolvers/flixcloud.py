from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

import httpx


ALLOWED_EMBED_HOSTS = {"flixcloud.cc", "www.flixcloud.cc"}
VIDEO_ID_PATTERNS = (
    # FlixCloud currently emits this as an unquoted JavaScript object key:
    # video_id:"<id>". Keep quoted-key variants for older embed versions.
    re.compile(r"""(?:["']video_id["']|video_id)\s*:\s*["']([A-Za-z0-9_-]{8,128})["']""", re.I),
    re.compile(r"""(?:["']videoId["']|videoId)\s*:\s*["']([A-Za-z0-9_-]{8,128})["']""", re.I),
)


class FlixCloudResolveError(RuntimeError):
    def __init__(self, message: str, status_code: int = 502):
        super().__init__(message)
        self.status_code = status_code


class FlixCloudResolver:
    """Inspect public FlixCloud embed metadata without bypassing player protections.

    This deliberately does not execute provider JavaScript/WASM or decrypt opaque
    playback payloads. A provider-approved playback interface can be added later.
    """

    def __init__(self, client: httpx.AsyncClient | None = None):
        self._client = client
        self._owns_client = client is None

    async def start(self) -> None:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(15.0),
                follow_redirects=False,
                headers={
                    "User-Agent": "Mozilla/5.0 (compatible; ReAnimeAPI/0.1)",
                    "Accept": "text/html,application/json;q=0.9,*/*;q=0.8",
                },
            )

    async def close(self) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None

    @staticmethod
    def validate_embed_url(embed_url: str) -> tuple[str, str]:
        try:
            parsed = urlparse(embed_url)
        except ValueError as exc:
            raise FlixCloudResolveError("Invalid FlixCloud embed URL", 400) from exc

        if parsed.scheme != "https" or parsed.hostname not in ALLOWED_EMBED_HOSTS:
            raise FlixCloudResolveError("Only HTTPS FlixCloud embed URLs are accepted", 400)

        match = re.fullmatch(r"/e/([A-Za-z0-9_-]{6,128})/?", parsed.path)
        if not match:
            raise FlixCloudResolveError("Unrecognized FlixCloud embed URL format", 400)

        return match.group(1), embed_url

    @staticmethod
    def _extract_video_id(html: str) -> str | None:
        for pattern in VIDEO_ID_PATTERNS:
            match = pattern.search(html)
            if match:
                return match.group(1)
        return None

    @staticmethod
    def _safe_metadata(payload: Any) -> dict[str, Any]:
        if not isinstance(payload, dict):
            return {}
        # FlixCloud's observed response nests descriptive metadata under
        # "metadata"; retain compatibility with flat response variants too.
        nested = payload.get("metadata")
        descriptive = nested if isinstance(nested, dict) else {}
        # Only expose non-secret descriptive fields. Opaque crypto/config blobs,
        # tokens, signed URLs, and raw provider payloads are intentionally omitted.
        allowed = ("version", "metadata_version", "duration", "title", "status", "timestamp")
        return {
            key: source[key]
            for key in allowed
            for source in (descriptive, payload)
            if isinstance(source.get(key), (str, int, float, bool))
        }

    async def resolve(self, embed_url: str) -> dict[str, Any]:
        embed_id, canonical_url = self.validate_embed_url(embed_url)
        if self._client is None:
            raise FlixCloudResolveError("FlixCloud resolver HTTP client has not started", 503)

        try:
            embed_response = await self._client.get(canonical_url)
        except httpx.TimeoutException as exc:
            raise FlixCloudResolveError("FlixCloud embed request timed out", 504) from exc
        except httpx.HTTPError as exc:
            raise FlixCloudResolveError(f"FlixCloud embed request failed ({type(exc).__name__})") from exc

        if embed_response.status_code in (401, 403, 429):
            raise FlixCloudResolveError(
                f"FlixCloud denied or rate-limited the embed request (HTTP {embed_response.status_code}); "
                "the resolver will not attempt to bypass that protection.",
                embed_response.status_code,
            )
        if not embed_response.is_success:
            raise FlixCloudResolveError(f"FlixCloud embed returned HTTP {embed_response.status_code}")

        video_id = self._extract_video_id(embed_response.text)
        if not video_id:
            return {
                "source": "flixcloud",
                "embed_id": embed_id,
                "video_id": None,
                "status": "metadata_unavailable",
                "playback_ready": False,
                "message": "The embed HTML did not expose a recognized video_id field.",
                "next_step": "Use the provider's documented playback integration or SDK.",
            }

        api_url = f"https://flixcloud.cc/api/m3u8/{video_id}"
        try:
            config_response = await self._client.get(
                api_url,
                headers={"Referer": canonical_url, "Accept": "application/json"},
            )
        except httpx.TimeoutException as exc:
            raise FlixCloudResolveError("FlixCloud playback metadata request timed out", 504) from exc
        except httpx.HTTPError as exc:
            raise FlixCloudResolveError(f"FlixCloud playback metadata request failed ({type(exc).__name__})") from exc

        if config_response.status_code in (401, 403, 429):
            raise FlixCloudResolveError(
                f"FlixCloud denied or rate-limited playback metadata (HTTP {config_response.status_code}).",
                config_response.status_code,
            )
        if not config_response.is_success:
            raise FlixCloudResolveError(
                f"FlixCloud playback metadata returned HTTP {config_response.status_code}"
            )
        try:
            payload = config_response.json()
        except ValueError as exc:
            raise FlixCloudResolveError("FlixCloud playback metadata was not valid JSON") from exc

        return {
            "source": "flixcloud",
            "embed_id": embed_id,
            "video_id": video_id,
            "status": "metadata_received",
            "playback_ready": False,
            "metadata": self._safe_metadata(payload),
            "message": (
                "FlixCloud returned playback metadata. The captured player uses an additional "
                "client-side protected processing stage; no direct stream URL is exposed by this resolver."
            ),
            "next_step": "Integrate the provider's approved playback resolver interface or SDK.",
        }
