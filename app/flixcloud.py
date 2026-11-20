from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

import httpx


class FlixCloudError(RuntimeError):
    def __init__(self, message: str, status_code: int = 502):
        super().__init__(message)
        self.status_code = status_code


class FlixCloudResolver:
    """
    Best-effort resolver for URLs that FlixCloud exposes directly in its
    playback JSON. It deliberately does not decode opaque player payloads,
    emulate browser challenges, or manufacture signed media URLs.
    """

    BASE_URL = "https://flixcloud.cc"
    VIDEO_ID_RE = re.compile(r"/e/([A-Za-z0-9_-]+)(?:/|$)")

    def __init__(self, timeout_seconds: float = 20.0):
        self.timeout_seconds = timeout_seconds

    @staticmethod
    def _walk(value: Any):
        if isinstance(value, dict):
            for key, child in value.items():
                yield str(key), child
                yield from FlixCloudResolver._walk(child)
        elif isinstance(value, list):
            for child in value:
                yield "", child
                yield from FlixCloudResolver._walk(child)

    @staticmethod
    def _absolute_url(value: Any) -> bool:
        if not isinstance(value, str):
            return False
        parsed = urlparse(value)
        host = (parsed.hostname or "").lower().rstrip(".")
        return (
            parsed.scheme == "https"
            and bool(host)
            and (host == "flixcloud.cc" or host.endswith(".flixcloud.cc"))
        )

    @classmethod
    def _extract_media(cls, payload: Any) -> tuple[str | None, list[dict[str, Any]]]:
        master: str | None = None
        subtitles: list[dict[str, Any]] = []
        seen: set[str] = set()

        for key, value in cls._walk(payload):
            if not cls._absolute_url(value):
                continue
            path = urlparse(value).path.lower()
            key_lower = key.lower()

            if path.endswith(".m3u8") and master is None:
                master = value
                continue

            if path.endswith((".vtt", ".srt", ".ass", ".ssa")):
                if value in seen:
                    continue
                seen.add(value)
                subtitles.append({
                    "url": value,
                    "lang": "English" if re.search(r"(?:eng|en)(?:_|\.|-)", path) else None,
                    "label": "English" if re.search(r"(?:eng|en)(?:_|\.|-)", path) else "Subtitle",
                })

        return master, subtitles

    async def resolve(self, embed_url: str) -> dict[str, Any]:
        if not self._absolute_url(embed_url):
            raise FlixCloudError("The selected server is not a valid HTTPS FlixCloud URL", 400)

        parsed = urlparse(embed_url)
        match = self.VIDEO_ID_RE.search(parsed.path)
        if not match:
            raise FlixCloudError("Could not identify the FlixCloud video ID in the server URL", 422)

        video_id = match.group(1)
        referer = embed_url

        headers = {
            "User-Agent": "Mozilla/5.0 (compatible; ReAnimeAPI/0.1)",
            "Accept": "application/json, text/plain, */*",
            "Referer": referer,
            "Origin": "https://flixcloud.cc",
        }

        api_url = f"{self.BASE_URL}/api/m3u8/{video_id}"

        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(self.timeout_seconds),
                follow_redirects=False,
            ) as client:
                response = await client.get(api_url, headers=headers)
        except httpx.TimeoutException as exc:
            raise FlixCloudError("FlixCloud playback API timed out", 504) from exc
        except httpx.HTTPError as exc:
            raise FlixCloudError(f"Could not reach FlixCloud playback API ({type(exc).__name__})", 502) from exc

        if response.status_code in (403, 429):
            raise FlixCloudError(
                f"FlixCloud denied or rate-limited the VPS request (HTTP {response.status_code}). "
                "The resolver will not attempt to bypass the provider's access controls.",
                response.status_code,
            )
        if not response.is_success:
            raise FlixCloudError(f"FlixCloud playback API returned HTTP {response.status_code}", 502)

        try:
            payload = response.json()
        except ValueError:
            payload = response.text

        master, subtitles = self._extract_media(payload)
        if not master:
            raise FlixCloudError(
                "FlixCloud returned playback metadata, but no directly exposed master.m3u8 URL "
                "or subtitle URLs were present. The captured response appears to contain an "
                "opaque player payload; a provider-authorized resolver/API response is required.",
                422,
            )

        return {"master_m3u8": master, "subtitles": subtitles}
