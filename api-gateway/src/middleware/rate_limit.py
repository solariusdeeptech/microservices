"""Simple in-memory rate limiter per platform."""
import time
from collections import defaultdict
from fastapi import Request
from fastapi.responses import JSONResponse
from loguru import logger

# {platform: [(timestamp, ...)]} — sliding window
_requests: dict[str, list[float]] = defaultdict(list)

# Limits per platform (requests per minute)
RATE_LIMITS = {
    "geoeconomix": 200,
    "geomatrix": 200,
    "terraexploration": 200,
    "_default": 60,
}
WINDOW = 60  # seconds


def check_rate_limit(platform: str) -> JSONResponse | None:
    """Return error response if rate limit exceeded, None otherwise."""
    now = time.time()
    window_start = now - WINDOW

    # Cleanup old entries
    _requests[platform] = [t for t in _requests[platform] if t > window_start]

    limit = RATE_LIMITS.get(platform, RATE_LIMITS["_default"])
    if len(_requests[platform]) >= limit:
        logger.warning(f"Rate limit exceeded for {platform}: {len(_requests[platform])}/{limit} req/min")
        return JSONResponse(
            status_code=429,
            content={
                "error": "Rate limit exceeded",
                "message": f"Max {limit} requests per minute. Retry after a few seconds.",
                "retry_after_seconds": int(WINDOW - (now - _requests[platform][0]))
            }
        )

    _requests[platform].append(now)
    return None
