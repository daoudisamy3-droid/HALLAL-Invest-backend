"""
Rate limiter — Redis-backed with in-memory fallback.

Strategy:
  1. If REDIS_URL / REDIS_PRIVATE_URL is set, use Redis INCR+EXPIRE
     (atomic, distributed, multi-instance safe).
  2. Otherwise fall back to in-memory sliding-window (single-instance only).

IP resolution: prefers the first IP from X-Forwarded-For so the real
client IP is used behind Railway's load balancer.
"""

import os
import time
from collections import defaultdict

from fastapi import HTTPException, Request

from app.core.config import get_settings
from app.core.logging import logger


# ── Redis connection (optional) ──────────────────────────────────

_redis_client = None
_USE_REDIS = False

_REDIS_URL = os.environ.get("REDIS_URL") or os.environ.get("REDIS_PRIVATE_URL")
if _REDIS_URL:
    try:
        import redis as _redis_lib
        _redis_client = _redis_lib.from_url(
            _REDIS_URL,
            decode_responses=True,
            socket_connect_timeout=3,
            socket_timeout=2,
        )
        _redis_client.ping()
        _USE_REDIS = True
        logger.info("rate-limiter: using Redis (%s...)", _REDIS_URL[:30])
    except Exception as exc:
        logger.warning("rate-limiter: Redis unavailable, falling back to in-memory (%s)", exc)
        _redis_client = None
        _USE_REDIS = False
else:
    logger.info("rate-limiter: no REDIS_URL set, using in-memory (single-instance only)")


# ── In-memory fallback ────────────────────────────────────────────

_mem_requests: dict[str, list[float]] = defaultdict(list)


# ── Helpers ───────────────────────────────────────────────────────

def _get_client_ip(request: Request) -> str:
    """Resolve real client IP, honouring X-Forwarded-For from Railway's proxy."""
    forwarded_for = request.headers.get("x-forwarded-for", "")
    if forwarded_for:
        return forwarded_for.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _check_redis(client_ip: str, limit: int) -> None:
    minute_bucket = int(time.time() / 60)
    key = f"ratelimit:{client_ip}:{minute_bucket}"
    try:
        count = _redis_client.incr(key)
        if count == 1:
            _redis_client.expire(key, 60)
        if count > limit:
            raise HTTPException(
                status_code=429,
                detail="Rate limit exceeded. Try again later.",
            )
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning("rate-limiter: Redis error, skipping check: %s", exc)


def _check_memory(client_ip: str, limit: int) -> None:
    now = time.time()
    window = 60.0
    _mem_requests[client_ip] = [
        ts for ts in _mem_requests[client_ip] if now - ts < window
    ]
    if len(_mem_requests[client_ip]) >= limit:
        raise HTTPException(
            status_code=429,
            detail="Rate limit exceeded. Try again later.",
        )
    _mem_requests[client_ip].append(now)


# ── FastAPI dependency ─────────────────────────────────────────────

async def rate_limit_dependency(request: Request) -> None:
    settings = get_settings()
    client_ip = _get_client_ip(request)
    if _USE_REDIS and _redis_client is not None:
        _check_redis(client_ip, settings.rate_limit_per_minute)
    else:
        _check_memory(client_ip, settings.rate_limit_per_minute)
