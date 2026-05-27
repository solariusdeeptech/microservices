"""
Authentification multi-tenant par X-API-Key
"""

import os
from fastapi import Request, HTTPException
from loguru import logger


def _load_api_keys() -> dict[str, str]:
    """Charge les clés API depuis les variables d'environnement."""
    keys = {}
    for key, value in os.environ.items():
        if key.startswith("API_KEY_"):
            platform = key.replace("API_KEY_", "").lower()
            keys[value] = platform
    return keys


API_KEYS = _load_api_keys()


async def verify_api_key(request: Request):
    """Dépendance FastAPI pour vérifier l'authentification."""
    api_key = request.headers.get("x-api-key", "")

    if not api_key:
        raise HTTPException(
            status_code=401,
            detail="Missing X-API-Key header. Authentication required."
        )

    if api_key not in API_KEYS:
        raise HTTPException(
            status_code=403,
            detail="Invalid API key. Access denied."
        )

    platform = API_KEYS[api_key]
    logger.info(f"Authenticated: platform={platform}")
    request.state.platform = platform
