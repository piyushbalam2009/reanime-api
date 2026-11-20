import json
import logging
from typing import Any
from redis.asyncio import Redis
from redis.exceptions import RedisError

logger = logging.getLogger('reanime.cache')

async def get_json(client: Redis | None, key: str) -> Any | None:
    if client is None:
        return None
    try:
        value = await client.get(key)
        return json.loads(value) if value else None
    except (RedisError, json.JSONDecodeError):
        logger.exception('Redis cache read failed')
        return None

async def set_json(client: Redis | None, key: str, value: Any, ttl_seconds: int) -> None:
    if client is None:
        return
    try:
        await client.set(key, json.dumps(value, separators=(',', ':')), ex=ttl_seconds)
    except (RedisError, TypeError):
        logger.exception('Redis cache write failed')
