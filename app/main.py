import logging
import time
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.cache import get_json, set_json
from app.config import settings
from app.source import ReAnimeAdapter, SourceNotConfigured, SourceRequestError
from app.resolvers.flixcloud import FlixCloudResolveError, FlixCloudResolver
from app.hls_proxy import hls_proxy, proxy_url_for, store_playlist_key

logging.basicConfig(level=getattr(logging, settings.log_level.upper(), logging.INFO))
logger = logging.getLogger("reanime.api")
adapter = ReAnimeAdapter()
flixcloud_resolver = FlixCloudResolver()
redis_client: Redis | None = None


@asynccontextmanager
async def lifespan(_: FastAPI):
    global redis_client
    await adapter.start()
    await flixcloud_resolver.start()
    if settings.redis_url:
        try:
            client = Redis.from_url(settings.redis_url, decode_responses=True, socket_connect_timeout=3)
            await client.ping()
            redis_client = client
            logger.info("Redis connected")
        except RedisError:
            logger.exception("Redis unavailable; cache disabled")
    yield
    if redis_client is not None:
        await redis_client.aclose()
        redis_client = None
    await adapter.close()
    await flixcloud_resolver.close()


app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    description="Re:ANIME catalog and server-discovery API.",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors,
    allow_credentials=settings.cors_origins.strip() != "*",
    allow_methods=["GET", "HEAD", "OPTIONS"],
    allow_headers=["*"],
)


@app.middleware("http")
async def public_rate_limit(request: Request, call_next):
    if request.url.path not in {"/", "/health", "/docs", "/redoc", "/openapi.json"} and redis_client is not None:
        forwarded = request.headers.get("x-forwarded-for", "")
        client_ip = forwarded.split(",")[0].strip() or (request.client.host if request.client else "unknown")
        bucket = int(time.time() // 60)
        key = f"rate:{client_ip}:{bucket}"
        try:
            count = await redis_client.incr(key)
            if count == 1:
                await redis_client.expire(key, 65)
            if count > 120:
                return JSONResponse(
                    status_code=429,
                    content={"error": "rate_limit_exceeded", "message": "Limit is 120 requests per minute."},
                    headers={"Retry-After": "60"},
                )
        except RedisError:
            logger.exception("Rate limiter unavailable; allowing request")
    return await call_next(request)


@app.get("/")
async def root() -> dict[str, Any]:
    return {"name": settings.app_name, "version": "0.1.0", "status": "running", "docs": "/docs"}


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/status")
async def status() -> dict[str, Any]:
    redis_ok = False
    if redis_client is not None:
        try:
            redis_ok = bool(await redis_client.ping())
        except RedisError:
            pass
    return {
        "api": "ok",
        "redis": "connected" if redis_ok else "unavailable",
        "source": {
            "website": settings.source_base_url,
            "adapter": "initialized",
            "access_mode": settings.source_access_mode,
            "browser_challenge_integration": "FlareSolverr",
        },
    }


@app.exception_handler(SourceRequestError)
async def source_error_handler(_: Request, exc: SourceRequestError):
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": "source_request_failed", "message": str(exc)},
    )


@app.exception_handler(FlixCloudResolveError)
async def flixcloud_error_handler(_: Request, exc: FlixCloudResolveError):
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": "flixcloud_resolve_failed", "message": str(exc)},
    )


def source_not_ready(exc: SourceNotConfigured) -> HTTPException:
    return HTTPException(
        status_code=501,
        detail={
            "error": "source_not_configured",
            "message": str(exc),
            "next_step": "Verify the site's actual public interface and permitted integration method.",
        },
    )


@app.get("/api/proxy/hls", name="hls_proxy")
async def proxy_hls(request: Request, url: str = Query(..., min_length=12, max_length=4096)):
    return await hls_proxy(request, url)


@app.get("/api/search")
async def search(q: str = Query(min_length=1, max_length=120), page: int = Query(1, ge=1)):
    key = f"search:{q.strip().lower()}:{page}"
    cached = await get_json(redis_client, key)
    if cached is not None:
        return cached
    try:
        result = {"query": q, "page": page, "results": await adapter.search(q, page)}
        await set_json(redis_client, key, result, 300)
        return result
    except SourceNotConfigured as exc:
        raise source_not_ready(exc) from exc


@app.get("/api/anime/{anime_id}")
async def anime_details(anime_id: str):
    key = f"anime:details:{anime_id}"
    cached = await get_json(redis_client, key)
    if cached is not None:
        return cached
    try:
        result = await adapter.details(anime_id)
        await set_json(redis_client, key, result, 21600)
        return result
    except SourceNotConfigured as exc:
        raise source_not_ready(exc) from exc


@app.get("/api/anime/{anime_id}/episodes")
async def episodes(anime_id: str):
    key = f"anime:episodes:{anime_id}"
    cached = await get_json(redis_client, key)
    if cached is not None:
        return cached
    try:
        result = {"anime_id": anime_id, "episodes": await adapter.episodes(anime_id)}
        await set_json(redis_client, key, result, 1800)
        return result
    except SourceNotConfigured as exc:
        raise source_not_ready(exc) from exc


@app.get("/api/anime/{anime_id}/server")
async def anime_server_compat(anime_id: str, episode: int = Query(1, ge=1)):
    """Compatibility route for clients using /api/anime/{id}/server?episode=1."""
    episode_id = f"{anime_id}:{episode}"
    key = f"episode:servers:{episode_id}"
    cached = await get_json(redis_client, key)
    if cached is not None:
        return cached
    try:
        result = await adapter.servers(episode_id)
        await set_json(redis_client, key, result, 120)
        return result
    except SourceNotConfigured as exc:
        raise source_not_ready(exc) from exc


@app.get("/api/episode/{episode_id}/servers")
async def servers(episode_id: str):
    key = f"episode:servers:{episode_id}"
    cached = await get_json(redis_client, key)
    if cached is not None:
        return cached
    try:
        result = await adapter.servers(episode_id)
        await set_json(redis_client, key, result, 120)
        return result
    except SourceNotConfigured as exc:
        raise source_not_ready(exc) from exc


@app.get("/api/episode/{episode_id}/resolve")
async def resolve_episode(
    request: Request,
    episode_id: str,
    audio: str = Query("sub", pattern="^(sub|dub)$"),
    server: str | None = Query(None, max_length=32),
):
    """Inspect a FlixCloud embed selected from the episode's server listing."""
    listing = await adapter.servers(episode_id)
    candidates = listing.get(audio) or []
    selected = next(
        (
            item for item in candidates
            if isinstance(item, dict)
            and item.get("dataLink")
            and (server is None or item.get("serverName") == server)
        ),
        None,
    )
    if selected is None:
        raise HTTPException(
            status_code=404,
            detail=f"No FlixCloud {audio} server was found for this episode and server selection.",
        )

    embed_url = selected.get("dataLink")
    result = await flixcloud_resolver.resolve(embed_url)
    playlist_key = result.pop("playlist_key", None)
    if playlist_key and result.get("video_id"):
        store_playlist_key(result["video_id"], playlist_key)
    result["url"] = proxy_url_for(request, result["url"])
    for subtitle in result.get("subtitles") or []:
        if isinstance(subtitle, dict) and subtitle.get("url"):
            subtitle["url"] = proxy_url_for(request, subtitle["url"])
    if result.get("thumbnails_vtt"):
        result["thumbnails_vtt"] = proxy_url_for(request, result["thumbnails_vtt"])
    return {
        "episode_id": episode_id,
        "audio": audio,
        "server": selected.get("serverName"),
        "playback": result,
    }
