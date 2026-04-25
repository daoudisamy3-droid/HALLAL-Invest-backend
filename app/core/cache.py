"""
Centralised TTL cache – single source of truth for all cached data.

Strategy:
  1. If REDIS_URL is set (Railway addon), use Redis with per-key TTL.
  2. Otherwise, fall back to an in-memory dict with manual TTL eviction.

Every service imports `cache_get` / `cache_set` from here instead of
maintaining its own dict+timestamp boilerplate.  Keys are namespaced
(`risk:AAPL`, `peers:MSFT`, `macro:commodities`, ...) so a single
store can serve every module without collisions.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any, Optional

from app.core.logging import logger

# ── Redis (optional) ─────────────────────────────────────────────

_redis_client: Any = None
_USE_REDIS = False

_REDIS_URL = os.environ.get("REDIS_URL") or os.environ.get("REDIS_PRIVATE_URL")
if _REDIS_URL:
    try:
        import redis

        _redis_client = redis.from_url(
            _REDIS_URL,
            decode_responses=True,
            socket_connect_timeout=3,
            socket_timeout=2,
        )
        _redis_client.ping()
        _USE_REDIS = True
        logger.info("cache: connected to Redis (%s)", _REDIS_URL[:30] + "...")
    except Exception as exc:
        logger.warning("cache: Redis unavailable, falling back to in-memory (%s)", exc)
        _redis_client = None
        _USE_REDIS = False


# ── In-memory fallback ───────────────────────────────────────────

_mem_store: dict[str, tuple[float, Any]] = {}


# ── Public API ───────────────────────────────────────────────────


def cache_get(namespace: str, key: str) -> Optional[Any]:
    """
    Retrieve a cached value.  Returns None on miss or expiry.

    `namespace` is a logical prefix (e.g. "risk", "peers", "macro").
    The full key is built as `namespace:key`.
    """
    full_key = f"{namespace}:{key}"

    if _USE_REDIS and _redis_client is not None:
        try:
            raw = _redis_client.get(full_key)
            if raw is None:
                return None
            return json.loads(raw)
        except Exception as exc:
            logger.debug("cache: redis GET error for %s: %s", full_key, exc)
            return None

    entry = _mem_store.get(full_key)
    if entry is None:
        return None
    ts, data = entry
    # TTL is embedded at write time; we store the expiry timestamp
    if datetime.now(timezone.utc).timestamp() > ts:
        _mem_store.pop(full_key, None)
        return None
    return data


def cache_set(namespace: str, key: str, data: Any, ttl: int = 60) -> None:
    """
    Store a value with a TTL in seconds.

    Redis: uses native SETEX for automatic expiry.
    In-memory: stores the expiry epoch so `cache_get` can evict lazily.
    """
    full_key = f"{namespace}:{key}"

    if _USE_REDIS and _redis_client is not None:
        try:
            _redis_client.setex(full_key, ttl, json.dumps(data, default=str))
            return
        except Exception as exc:
            logger.debug("cache: redis SET error for %s: %s", full_key, exc)
            # fall through to in-memory

    expiry = datetime.now(timezone.utc).timestamp() + ttl
    _mem_store[full_key] = (expiry, data)


def cache_clear(namespace: Optional[str] = None) -> int:
    """
    Flush cached entries.  If `namespace` is given, only keys under that
    prefix are removed.  Returns the number of evicted entries.
    """
    if _USE_REDIS and _redis_client is not None:
        try:
            pattern = f"{namespace}:*" if namespace else "*"
            keys = list(_redis_client.scan_iter(match=pattern, count=200))
            if keys:
                _redis_client.delete(*keys)
            return len(keys)
        except Exception as exc:
            logger.debug("cache: redis CLEAR error: %s", exc)

    if namespace is None:
        count = len(_mem_store)
        _mem_store.clear()
        return count

    prefix = f"{namespace}:"
    victims = [k for k in _mem_store if k.startswith(prefix)]
    for k in victims:
        del _mem_store[k]
    return len(victims)
