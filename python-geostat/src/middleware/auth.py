"""
Multi-tenant API key authentication.
Checks X-API-Key header against configured platform keys.
"""
import os
import logging
from fastapi import Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)

def _load_api_keys() -> dict[str, str]:
    keys = {}
    for k, v in os.environ.items():
        if k.startswith("API_KEY_"):
            platform = k.replace("API_KEY_", "").lower()
            keys[v] = platform
    return keys

API_KEYS = _load_api_keys()

def verify_api_key(request: Request):
    api_key = request.headers.get("x-api-key", "")
    if not api_key:
        return JSONResponse(
            status_code=401,
            content={"error": "Missing X-API-Key header", "message": "Authentication required."}
        )
    if api_key not in API_KEYS:
        return JSONResponse(
            status_code=403,
            content={"error": "Invalid API key", "message": "The provided API key is not authorized."}
        )
    platform = API_KEYS[api_key]
    logger.info(f"Authenticated request from platform: {platform}")
    return None
