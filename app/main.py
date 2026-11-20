import json
import logging
import time
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic_settings import BaseSettings, SettingsConfigDict
from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.cache import get_json, set_json
from app.config import settings
from app.source import ReAnimeAdapter, SourceNotConfigured
logging.basicConfig(level=getattr(logging, settings.log_level.upper(), logging.INFO))
logger = logging.getLogger("reanime.api")
adapter = ReAnimeAdapter()
redis_client: Redis | None = None

@asynccontextmanager
async def lifespan(_: FastAPI):
    global redis_client
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

app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    description="Public Re:ANIME API. Source integration is being verified.",
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
    # Open API: no key required. Redis provides a shared per-IP limit across instances.
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
            "adapter": "not_configured",
            "access_mode": settings.source_access_mode,
            "browser_challenge_integration": "disabled",
        },
        "streaming_proxy": bool(settings.stream_proxy_base_url and settings.proxy_signing_secret),
    }

def source_not_ready(exc: SourceNotConfigured) -> HTTPException:
    return HTTPException(
        status_code=501,
        detail={
            "error": "source_not_configured",
            "message": str(exc),
            "next_step": "Verify the site's actual public interface and permitted integration method.",
        },
    )

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

@app.get("/api/watch/{episode_id}/{audio}")
async def watch(episode_id: str, audio: str):
    if audio not in {"sub", "dub"}:
        raise HTTPException(status_code=400, detail="audio must be sub or dub")
    try:
        data = await adapter.playback(episode_id, audio)
    except SourceNotConfigured as exc:
        raise source_not_ready(exc) from exc
    # Only the verified source adapter may supply this URL.
    master = data.get("master_m3u8")
    if not master:
        raise HTTPException(status_code=502, detail="No master HLS playlist was returned")
    from app.security import ProxyTokenError, create_proxy_token
    if not settings.stream_proxy_base_url:
        raise HTTPException(status_code=503, detail="Streaming proxy is not configured")
    try:
        token = create_proxy_token(master)
        proxy_base = settings.stream_proxy_base_url.rstrip("/")
        subtitles = []
        for track in data.get("subtitles", []):
            item = dict(track)
            subtitle_url = item.get("url")
            if subtitle_url:
                item["url"] = f"{proxy_base}/proxy/{create_proxy_token(subtitle_url)}"
            subtitles.append(item)
    except (ProxyTokenError, TypeError, ValueError) as exc:
        raise HTTPException(status_code=503, detail="Playback resource could not be safely proxied") from exc
    return {
        "episode_id": episode_id,
        "audio": audio,
        "server": data.get("server"),
        "playback": {
            "type": "hls",
            "master_m3u8": {"proxy_url": f"{proxy_base}/proxy/{token}", "format": "m3u8"},
            "subtitles": subtitles,
        },
    }
