"""Multi-tenant API key authentication middleware."""
import time
from loguru import logger
from fastapi import Request
from fastapi.responses import JSONResponse
from src.config import settings

API_KEYS = settings.get_api_keys()


def verify_api_key(request: Request) -> tuple[str | None, JSONResponse | None]:
    """
    Validate X-API-Key header.
    Returns (platform_name, None) on success or (None, error_response) on failure.
    """
    api_key = request.headers.get("x-api-key", "")
    if not api_key:
        return None, JSONResponse(
            status_code=401,
            content={"error": "Missing X-API-Key header", "message": "Authentication required."}
        )
    platform = API_KEYS.get(api_key)
    if not platform:
        return None, JSONResponse(
            status_code=403,
            content={"error": "Invalid API key", "message": "The provided API key is not authorized."}
        )
    return platform, None


async def auth_middleware(request: Request, call_next):
    """FastAPI middleware: authenticate and tag request with platform."""
    if request.url.path in ("/health", "/docs", "/openapi.json") or request.method == "OPTIONS":
        return await call_next(request)

    platform, error = verify_api_key(request)
    if error:
        return error

    # Inject platform into request state
    request.state.platform = platform
    request.state.start_time = time.time()

    response = await call_next(request)

    # Add tracing headers
    elapsed = time.time() - request.state.start_time
    response.headers["X-Platform"] = platform
    response.headers["X-Processing-Time-Ms"] = str(int(elapsed * 1000))

    logger.info(f"[{platform}] {request.method} {request.url.path} → {response.status_code} ({int(elapsed*1000)}ms)")
    return response
