from fastapi import Request, HTTPException
import time
from collections import defaultdict

from app.core.config import get_settings


class RateLimiter:
    """Simple in-memory sliding-window rate limiter."""

    def __init__(self) -> None:
        self._requests: dict[str, list[float]] = defaultdict(list)

    def check(self, client_ip: str) -> None:
        settings = get_settings()
        now = time.time()
        window = 60.0

        # Prune expired timestamps
        self._requests[client_ip] = [
            ts for ts in self._requests[client_ip] if now - ts < window
        ]

        if len(self._requests[client_ip]) >= settings.rate_limit_per_minute:
            raise HTTPException(
                status_code=429,
                detail="Rate limit exceeded. Try again later.",
            )

        self._requests[client_ip].append(now)


rate_limiter = RateLimiter()


async def rate_limit_dependency(request: Request) -> None:
    client_ip = request.client.host if request.client else "unknown"
    rate_limiter.check(client_ip)
