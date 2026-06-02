"""
Cloud Run proxy — forwards requests to python-geostat or python-viz.
Used for lightweight jobs (small datasets, fast responses).
"""
import httpx
from loguru import logger
from fastapi import Request
from fastapi.responses import Response, JSONResponse
from src.config import settings

# Persistent HTTP client with connection pooling
_client = httpx.AsyncClient(
    timeout=httpx.Timeout(settings.REQUEST_TIMEOUT, connect=10.0),
    limits=httpx.Limits(max_connections=50, max_keepalive_connections=20),
    follow_redirects=True,
)

# Service routing map: endpoint prefix → backend URL
SERVICE_MAP = {
    # Python Geostat endpoints
    "variography": settings.PYTHON_GEOSTAT_URL,
    "kriging": settings.PYTHON_GEOSTAT_URL,
    "sgs": settings.PYTHON_GEOSTAT_URL,
    "montecarlo": settings.PYTHON_GEOSTAT_URL,
    "pit-optimize": settings.PYTHON_GEOSTAT_URL,
    "block-model": settings.PYTHON_GEOSTAT_URL,
    "blockmodel": settings.PYTHON_GEOSTAT_URL,
    "ml-domaining": settings.PYTHON_GEOSTAT_URL,
    "deep-kriging": settings.PYTHON_GEOSTAT_URL,
    "spatial-continuity": settings.PYTHON_GEOSTAT_URL,
    "hybrid-clustering": settings.PYTHON_GEOSTAT_URL,
    "envelope-geometry": settings.PYTHON_GEOSTAT_URL,
    # Python Viz endpoints
    "render-3d": settings.PYTHON_VIZ_URL,
    "sections": settings.PYTHON_VIZ_URL,
    "drillholes": settings.PYTHON_VIZ_URL,
    "mps": settings.PYTHON_VIZ_URL,
}


def resolve_backend(path: str) -> str | None:
    """Determine which backend handles a given API path."""
    # Strip leading /api/ or / prefix
    clean = path.lstrip("/")
    if clean.startswith("api/"):
        clean = clean[4:]

    # Check each service prefix
    for prefix, url in SERVICE_MAP.items():
        if clean.startswith(prefix):
            return url
    return None


async def proxy_to_cloud_run(
    request: Request,
    backend_url: str,
    target_path: str,
) -> Response:
    """
    Forward an HTTP request to a Cloud Run backend and stream the response back.
    """
    # Build target URL
    url = f"{backend_url.rstrip('/')}/{target_path.lstrip('/')}"

    # Forward headers (preserve API key for backend auth)
    headers = {
        "x-api-key": request.headers.get("x-api-key", ""),
        "content-type": request.headers.get("content-type", "application/json"),
        "x-platform": getattr(request.state, "platform", "unknown"),
        "x-forwarded-for": request.client.host if request.client else "unknown",
    }

    # Read body
    body = await request.body()

    try:
        resp = await _client.request(
            method=request.method,
            url=url,
            headers=headers,
            content=body if request.method in ("POST", "PUT", "PATCH") else None,
            params=dict(request.query_params) if request.query_params else None,
        )

        # Stream response back
        response_headers = {
            "x-backend": "cloud-run",
            "x-backend-status": str(resp.status_code),
        }

        return Response(
            content=resp.content,
            status_code=resp.status_code,
            headers=response_headers,
            media_type=resp.headers.get("content-type", "application/json"),
        )

    except httpx.TimeoutException:
        logger.error(f"Timeout proxying to {url}")
        return JSONResponse(
            status_code=504,
            content={"error": "Backend timeout", "message": f"Cloud Run backend at {backend_url} timed out after {settings.REQUEST_TIMEOUT}s"}
        )
    except httpx.ConnectError:
        logger.error(f"Connection refused: {url}")
        return JSONResponse(
            status_code=502,
            content={"error": "Backend unavailable", "message": f"Could not connect to backend at {backend_url}"}
        )
    except Exception as e:
        logger.exception(f"Proxy error: {e}")
        return JSONResponse(
            status_code=500,
            content={"error": "Gateway error", "message": str(e)}
        )


async def check_backend_health(name: str, url: str) -> dict:
    """Check health of a Cloud Run backend."""
    try:
        resp = await _client.get(f"{url}/health", timeout=5.0)
        data = resp.json()
        return {"name": name, "status": "healthy", "url": url, **data}
    except Exception as e:
        return {"name": name, "status": "unhealthy", "url": url, "error": str(e)}
