from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

import httpx

from app.config import settings


class SourceNotConfigured(RuntimeError):
    """The source client has not been initialized."""


class SourceRequestError(RuntimeError):
    def __init__(self, message: str, status_code: int = 502):
        super().__init__(message)
        self.status_code = status_code


class ReAnimeAdapter:
    """Client for Re:ANIME's public JSON catalog and server-list interfaces.

    Embed-only player responses are returned as embeds. This adapter does not
    reverse-engineer third-party player encryption or synthesize media URLs.
    """

    def __init__(self, client: httpx.AsyncClient | None = None):
        self._client = client
        self._owns_client = client is None

    async def start(self) -> None:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=settings.source_base_url.rstrip("/"),
                timeout=httpx.Timeout(settings.source_timeout_seconds),
                follow_redirects=True,
                headers={
                    "User-Agent": "Mozilla/5.0 (compatible; ReAnimeAPI/0.1)",
                    "Accept": "application/json, text/plain, */*",
                    "Referer": settings.source_base_url.rstrip("/") + "/",
                },
            )

    async def close(self) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        if self._client is None:
            raise SourceNotConfigured("Source HTTP client has not been started")
        try:
            response = await self._client.get(path, params=params)
        except httpx.TimeoutException as exc:
            raise SourceRequestError("Re:ANIME request timed out", 504) from exc
        except httpx.HTTPError as exc:
            raise SourceRequestError(f"Could not reach Re:ANIME ({type(exc).__name__})", 502) from exc
        if response.status_code == 404:
            raise SourceRequestError("Re:ANIME resource not found", 404)
        if response.status_code in (403, 429):
            raise SourceRequestError(f"Re:ANIME denied or rate-limited the request (HTTP {response.status_code})", response.status_code)
        if not response.is_success:
            raise SourceRequestError(f"Re:ANIME returned HTTP {response.status_code}", 502)
        try:
            return response.json()
        except ValueError as exc:
            raise SourceRequestError("Re:ANIME returned a non-JSON response", 502) from exc

    @staticmethod
    def _items(data: Any, *keys: str) -> list[dict[str, Any]]:
        if isinstance(data, list):
            return [x for x in data if isinstance(x, dict)]
        if isinstance(data, dict):
            for key in keys:
                if isinstance(data.get(key), list):
                    return [x for x in data[key] if isinstance(x, dict)]
        return []

    @staticmethod
    def _id(item: dict[str, Any]) -> str | None:
        value = item.get("anime_id") or item.get("id") or item.get("slug")
        return str(value) if value is not None else None

    @staticmethod
    def _anilist_id(anime: dict[str, Any]) -> int | None:
        value = anime.get("anilist_id") or anime.get("anilist")
        try:
            if value:
                return int(value)
        except (TypeError, ValueError):
            pass
        cover = anime.get("cover_image") or {}
        if isinstance(cover, dict):
            for key in ("extra_large", "large", "medium"):
                match = re.search(r"anilist\.co/.*?/bx(\d+)-", cover.get(key) or "")
                if match:
                    return int(match.group(1))
        return None

    async def search(self, query: str, page: int = 1) -> list[dict[str, Any]]:
        limit = 20
        data = await self._get("/api/v1/search", {
            "q": query.strip(), "limit": limit, "offset": (page - 1) * limit
        })
        results = self._items(data, "results", "data", "items")
        return [{**x, "id": self._id(x)} for x in results if self._id(x)]

    async def details(self, anime_id: str) -> dict[str, Any]:
        self._validate_id(anime_id)
        data = await self._get(f"/api/v1/anime/{anime_id}")
        if not isinstance(data, dict):
            raise SourceRequestError("Unexpected anime detail response", 502)
        result = dict(data)
        result.setdefault("id", anime_id)
        result["anilist_id"] = self._anilist_id(result)
        return result

    async def episodes(self, anime_id: str) -> list[dict[str, Any]]:
        self._validate_id(anime_id)
        data = await self._get(f"/api/v1/anime/{anime_id}/episodes", {"limit": 2000})
        items = self._items(data, "data", "episodes", "results")
        output = []
        for item in items:
            number = item.get("episode_number", item.get("number"))
            if number is None:
                continue
            output.append({**item, "id": f"{anime_id}:{number}", "anime_id": anime_id, "number": number})
        return sorted(output, key=lambda x: float(x["number"]))

    async def servers(self, episode_id: str) -> dict[str, Any]:
        anime_id, episode = self._parse_episode_id(episode_id)
        watch = await self._get(f"/api/watch/{anime_id}/{episode}")
        if not isinstance(watch, dict):
            raise SourceRequestError("Unexpected server-list response", 502)
        links = list(watch.get("episode_links") or [])
        aid = self._anilist_id(watch.get("anime") or {})
        if aid:
            try:
                extra = await self._get(f"/api/flix/{aid}/{episode}")
                if isinstance(extra, dict) and extra.get("success"):
                    known = {x.get("$id") for x in links if isinstance(x, dict)}
                    links.extend(x for x in extra.get("servers", []) if isinstance(x, dict) and x.get("$id") not in known)
            except SourceRequestError as exc:
                if exc.status_code not in (403, 404, 429):
                    raise
        priority = {"HD-2": 0, "HD-1": 1}
        sub = [x for x in links if isinstance(x, dict) and x.get("dataType") in ("sub", "s-sub")]
        dub = [x for x in links if isinstance(x, dict) and x.get("dataType") in ("dub", "s-dub")]
        return {
            "episode_id": episode_id,
            "anime": watch.get("anime"),
            "anilist_id": aid,
            "sub": sorted(sub, key=lambda x: priority.get(x.get("serverName"), 9)),
            "dub": sorted(dub, key=lambda x: priority.get(x.get("serverName"), 9)),
            "intro_start": watch.get("intro_start"),
            "intro_end": watch.get("intro_end"),
            "outro_start": watch.get("outro_start"),
            "outro_end": watch.get("outro_end"),
        }

    async def playback(self, episode_id: str, audio: str) -> dict[str, Any]:
        if audio not in ("sub", "dub"):
            raise SourceRequestError("audio must be sub or dub", 400)
        data = await self.servers(episode_id)
        candidates = data[audio]
        if not candidates:
            raise SourceRequestError(f"No {audio} server is available", 404)
        for server in candidates:
            url = server.get("master_m3u8") or server.get("m3u8") or server.get("url")
            if self._valid_url(url) and ".m3u8" in urlparse(url).path.lower():
                return {"master_m3u8": url, "server": server.get("serverName"), "subtitles": server.get("subtitles") or []}
        chosen = candidates[0]
        embed = chosen.get("dataLink") or chosen.get("embedUrl") or chosen.get("embed")
        if self._valid_url(embed):
            return {"embed_url": embed, "server": chosen.get("serverName"), "subtitles": [], "message": "Upstream exposes an embed player, not a direct HLS URL."}
        raise SourceRequestError("No usable playback URL was exposed by Re:ANIME", 502)

    @staticmethod
    def _valid_url(value: Any) -> bool:
        if not isinstance(value, str):
            return False
        parsed = urlparse(value)
        return parsed.scheme in ("http", "https") and bool(parsed.netloc)

    @staticmethod
    def _validate_id(value: str) -> None:
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,160}", value):
            raise SourceRequestError("Invalid Re:ANIME anime ID", 400)

    @classmethod
    def _parse_episode_id(cls, value: str) -> tuple[str, int]:
        match = re.fullmatch(r"([A-Za-z0-9_-]{1,160}):(\d+(?:\.\d+)?)", value)
        if not match:
            raise SourceRequestError("episode_id must use anime_id:episode_number", 400)
        return match.group(1), int(float(match.group(2)))
