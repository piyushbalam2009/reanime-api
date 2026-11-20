import json
import logging
from typing import Any

from redis.exceptions import RedisError
from app.main import redis_client

logger = logging.getLogger('reanime.cache')

async def get_json(key: str) -> Any | None:
    if redis_client is None:
        return None
    try:
        value = await redis_client.get(key)
        return json.loads(value) if value else None
    except (RedisError, json.JSONDecodeError):
        logger.exception('Redis cache read failed')
        return None

async def set_json(key: str, value: Any, ttl_seconds: int) -> None:
    if redis_client is None:
        return
    try:
        await redis_client.set(key, json.dumps(value, separators=(',', ':')), ex=ttl_seconds)
    except (RedisError, TypeError):
        logger.exception('Redis cache write failed')
