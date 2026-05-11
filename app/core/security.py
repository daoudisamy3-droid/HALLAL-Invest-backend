"""
API key guard + in-memory rate limiter  §9.6.

verify_api_key — FastAPI dependency used on the entire /api/v1 router.
RateLimiter    — sliding-window counter keyed by arbitrary string (IP, token…).
"""

import time
from collections import defaultdict
from fastapi import Header, HTTPException, Request, status


class RateLimiter:
    """Sliding-window rate limiter (in-process, non-distributed)."""

    def __init__(self, max_calls: int = 60, period: float = 60.0) -> None:
        self._max_calls = max_calls
        self._period = period
        self._calls: dict[str, list[float]] = defaultdict(list)

    def is_allowed(self, key: str) -> bool:
        now = time.monotonic()
        window = self._calls[key]
        self._calls[key] = [t for t in window if now - t < self._period]
        if len(self._calls[key]) >= self._max_calls:
            return False
        self._calls[key].append(now)
        return True


rate_limiter = RateLimiter(max_calls=60, period=60.0)


async def verify_api_key(
    request: Request,
    x_auth_token: str | None = Header(default=None),
) -> None:
    """
    Dependency injected on every /api/v1 route.

    1. Rejects missing or wrong X-Auth-Token with 401.
    2. Applies the global rate limiter keyed by (token or client IP).
    """
    from app.core.config import settings  # late import avoids circular dep

    if x_auth_token != settings.FINTERMINAL_API_KEY:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing API key",
            headers={"WWW-Authenticate": "ApiKey"},
        )

    rate_key = x_auth_token or request.client.host if request.client else "anon"
    if not rate_limiter.is_allowed(rate_key):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Rate limit exceeded — retry after 60 s",
        )
