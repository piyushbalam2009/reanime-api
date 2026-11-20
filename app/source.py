from typing import Any


class SourceNotConfigured(RuntimeError):
    """Raised until real Re:ANIME public interfaces are mapped and verified."""


class ReAnimeAdapter:
    """Keep source-specific parsing isolated from public API route handlers.

    Implement methods only after verifying real responses and the site's permitted
    integration method. Never infer endpoints from a different ReAnime domain.
    """

    async def search(self, query: str, page: int = 1) -> list[dict[str, Any]]:
        raise SourceNotConfigured("Re:ANIME search interface has not been verified")

    async def details(self, anime_id: str) -> dict[str, Any]:
        raise SourceNotConfigured("Re:ANIME detail interface has not been verified")

    async def episodes(self, anime_id: str) -> list[dict[str, Any]]:
        raise SourceNotConfigured("Re:ANIME episode interface has not been verified")

    async def servers(self, episode_id: str) -> dict[str, Any]:
        raise SourceNotConfigured("Re:ANIME server interface has not been verified")

    async def playback(self, episode_id: str, audio: str) -> dict[str, Any]:
        raise SourceNotConfigured("Re:ANIME playback interface has not been verified")
