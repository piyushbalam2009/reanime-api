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

    async def _get(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> Any:
        if self._client is None:
            raise SourceNotConfigured("Source HTTP client has not been started")
        try:
            response = await self._client.get(path, params=params, headers=headers)
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
            source_episode_id = item.get("episodeId") or item.get("episode_id") or item.get("id")
            output.append({
                **item,
                "id": f"{anime_id}:{number}",
                "anime_id": anime_id,
                "number": number,
                "source_episode_id": str(source_episode_id) if source_episode_id is not None else None,
            })
        return sorted(output, key=lambda x: float(x["number"]))

    async def servers(self, episode_id: str) -> dict[str, Any]:
        anime_id, episode = self._parse_episode_id(episode_id)
        watch_referer = f"{settings.source_base_url.rstrip('/')}/watch/{anime_id}?ep={episode}"
        watch: dict[str, Any] = {}
        links: list[dict[str, Any]] = []
        aid: int | None = None

        # The watch JSON route may be unavailable on some Re:ANIME deployments.
        # In that case, resolve the AniList ID from catalog metadata and use the
        # public Flix server-list endpoint directly.
        try:
            candidate = await self._get(
                f"/api/watch/{anime_id}/{episode}",
                headers={"Referer": watch_referer},
            )
            if not isinstance(candidate, dict):
                raise SourceRequestError("Unexpected server-list response", 502)
            watch = candidate
            links = [x for x in (watch.get("episode_links") or []) if isinstance(x, dict)]
            aid = self._anilist_id(watch.get("anime") or {})
        except SourceRequestError as exc:
            if exc.status_code != 404:
                raise
            anime = await self.details(anime_id)
            anime_data = anime.get("anime") if isinstance(anime.get("anime"), dict) else anime
            aid = self._anilist_id(anime_data)
            if not aid:
                raise SourceRequestError(
                    "The watch route is unavailable and catalog metadata has no AniList ID; "
                    "cannot look up Re:ANIME servers.",
                    404,
                ) from exc
            watch = {"anime": anime_data}

        if aid:
            try:
                extra = await self._get(
                    f"/api/flix/{aid}/{episode}",
                    headers={"Referer": watch_referer},
                )
                if isinstance(extra, dict) and extra.get("success"):
                    extra_servers = extra.get("servers") or []
                    known = {
                        (x.get("$id"), x.get("dataLink"), x.get("dataType"))
                        for x in links
                    }
                    for item in extra_servers:
                        if not isinstance(item, dict):
                            continue
                        identity = (item.get("$id"), item.get("dataLink"), item.get("dataType"))
                        if identity not in known:
                            links.append(item)
                            known.add(identity)
            except SourceRequestError as exc:
                # A Flix lookup is optional when the watch route already supplied
                # usable servers. It is required for the fallback path.
                if not links or exc.status_code not in (403, 404, 429):
                    raise

        priority = {"HD-2": 0, "HD-1": 1}
        sub = [x for x in links if x.get("dataType") in ("sub", "s-sub")]
        dub = [x for x in links if x.get("dataType") in ("dub", "s-dub")]
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

    @classmethod
    def _find_master_m3u8(cls, value: Any) -> str | None:
        """Find an explicitly exposed HLS playlist URL without treating embeds as streams."""
        if isinstance(value, dict):
            # Prefer fields explicitly named as HLS/master playlist URLs.
            preferred = ("master_m3u8", "masterM3U8", "m3u8", "hls_url", "hlsUrl")
            for key in preferred:
                candidate = value.get(key)
                if cls._valid_url(candidate) and ".m3u8" in urlparse(candidate).path.lower():
                    return candidate
            for child in value.values():
                found = cls._find_master_m3u8(child)
                if found:
                    return found
        elif isinstance(value, list):
            for child in value:
                found = cls._find_master_m3u8(child)
                if found:
                    return found
        elif isinstance(value, str) and cls._valid_url(value):
            if ".m3u8" in urlparse(value).path.lower():
                return value
        return None

    @classmethod
    def _extract_subtitles(cls, server: dict[str, Any]) -> list[dict[str, Any]]:
        raw = server.get("subtitles") or server.get("tracks") or server.get("captions") or []
        if isinstance(raw, dict):
            raw = raw.get("tracks") or raw.get("subtitles") or []
        if not isinstance(raw, list):
            return []
        result = []
        for track in raw:
            if isinstance(track, str):
                url = track
                item: dict[str, Any] = {"url": url}
            elif isinstance(track, dict):
                item = dict(track)
                url = item.get("url") or item.get("file") or item.get("src")
                if not url:
                    continue
                item["url"] = url
            else:
                continue
            if cls._valid_url(url):
                result.append(item)
        return result

    async def playback(self, episode_id: str, audio: str) -> dict[str, Any]:
        if audio not in ("sub", "dub"):
            raise SourceRequestError("audio must be sub or dub", 400)
        data = await self.servers(episode_id)
        candidates = data[audio]
        if not candidates:
            raise SourceRequestError(f"No {audio} server is available", 404)
        for server in candidates:
            # Walk provider metadata for a real .m3u8 URL. Never return dataLink/embed URLs.
            master = self._find_master_m3u8(server)
            if master:
                return {
                    "master_m3u8": master,
                    "server": server.get("serverName"),
                    "subtitles": self._extract_subtitles(server),
                }
        raise SourceRequestError(
            f"No direct master.m3u8 URL was exposed for {audio}; provider returned no usable HLS playlist. Embed links are intentionally not returned.",
            502,
        )

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
        match = re.fullmatch(r"([A-Za-z0-9_-]{1,160}):(?:ep-)?(\d+(?:\.\d+)?)", value)
        if not match:
            raise SourceRequestError("episode_id must use anime_id:episode_number or anime_id:ep-number", 400)
        return match.group(1), int(float(match.group(2)))
