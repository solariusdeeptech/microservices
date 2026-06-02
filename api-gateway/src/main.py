"""
Solarius API Gateway — Intelligent routing for 3 platforms.

Routes:
  - Lightweight jobs → Cloud Run (python-geostat, python-viz) [sync]
  - Heavy jobs → Cloud Batch (Julia / Python) [async: submit/poll/result]
  - All platforms authenticated via X-API-Key
"""
import sys
import time
import json
from datetime import datetime, timezone

from fastapi import FastAPI, Request, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from loguru import logger

from src.config import settings
from src.middleware.auth import auth_middleware
from src.middleware.rate_limit import check_rate_limit
from src.services.cloud_run import proxy_to_cloud_run, resolve_backend, check_backend_health
from src.services.cloud_batch import (
    submit_batch_job, get_job_status, get_job_result,
    CLOUD_BATCH_AVAILABLE,
)
from src.services.router import decide_route, estimate_payload_size
from src.metrics.usage import record_request, get_metrics

logger.remove()
logger.add(sys.stderr, level=settings.LOG_LEVEL, format="{time:HH:mm:ss} | {level:<7} | {message}")

app = FastAPI(
    title="Solarius API Gateway",
    version="1.0.0",
    description="Intelligent routing gateway for Solarius microservices (GEO-ECONOMIX, GeoMatrix, TerraExploration)",
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Auth middleware
app.middleware("http")(auth_middleware)


# ============================================================
# Health & Info
# ============================================================

@app.get("/health")
async def health():
    """Gateway health check — includes backend status."""
    backends = [
        await check_backend_health("python-geostat", settings.PYTHON_GEOSTAT_URL),
        await check_backend_health("python-viz", settings.PYTHON_VIZ_URL),
    ]
    all_healthy = all(b["status"] == "healthy" for b in backends)
    return {
        "status": "healthy" if all_healthy else "degraded",
        "service": "api-gateway",
        "version": "1.0.0",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "cloud_batch_available": CLOUD_BATCH_AVAILABLE,
        "backends": backends,
        "routing_thresholds": {
            "max_points_cloud_run": settings.MAX_POINTS_CLOUD_RUN,
            "max_blocks_cloud_run": settings.MAX_BLOCKS_CLOUD_RUN,
            "max_realizations_cloud_run": settings.MAX_REALIZATIONS_CLOUD_RUN,
        },
    }


@app.get("/info")
async def info():
    """Gateway capabilities and configuration."""
    return {
        "service": "Solarius API Gateway",
        "version": "1.0.0",
        "platforms": ["geoeconomix", "geomatrix", "terraexploration"],
        "backends": {
            "cloud_run": [
                {"name": "python-geostat", "url": settings.PYTHON_GEOSTAT_URL},
                {"name": "python-viz", "url": settings.PYTHON_VIZ_URL},
            ],
            "cloud_batch": {
                "available": CLOUD_BATCH_AVAILABLE,
                "julia_image": settings.JULIA_IMAGE,
                "python_image": settings.PYTHON_HEAVY_IMAGE,
            },
        },
        "endpoints": {
            "sync": [
                "/api/variography", "/api/kriging", "/api/sgs",
                "/api/montecarlo", "/api/pit-optimize",
                "/api/block-model", "/api/blockmodel",
                "/api/ml-domaining", "/api/deep-kriging",
                "/api/spatial-continuity", "/api/hybrid-clustering",
                "/api/envelope-geometry",
                "/api/boolean-ops", "/api/faults", "/api/intervalmaker", "/api/stratamind-profiles",
                "/api/render-3d", "/api/sections", "/api/drillholes", "/api/mps",
            ],
            "async": [
                "/api/v1/jobs/submit",
                "/api/v1/jobs/{job_id}/status",
                "/api/v1/jobs/{job_id}/result",
            ],
        },
    }


# ============================================================
# Metrics
# ============================================================

@app.get("/api/v1/metrics")
async def api_metrics(request: Request, platform: str | None = None):
    """Usage metrics. If platform not specified, returns all."""
    return get_metrics(platform)


# ============================================================
# Route Analysis (dry-run)
# ============================================================

@app.post("/api/v1/analyze")
async def analyze_route(request: Request, endpoint: str = Query(...)):
    """
    Analyze a payload WITHOUT executing it.
    Returns routing decision (sync/async), estimated complexity, runtime.
    """
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    decision = decide_route(endpoint, payload)
    return {
        "endpoint": endpoint,
        "decision": decision,
        "cloud_batch_available": CLOUD_BATCH_AVAILABLE,
    }


# ============================================================
# Async Jobs (Cloud Batch)
# ============================================================

@app.post("/api/v1/jobs/submit")
async def submit_job(request: Request):
    """
    Submit a heavy computation job to Cloud Batch.
    Body: {endpoint, payload, runtime?, machine_type?}
    """
    body = await request.json()
    endpoint = body.get("endpoint")
    payload = body.get("payload", {})
    runtime = body.get("runtime")  # optional override
    machine_type = body.get("machine_type")  # optional override

    if not endpoint:
        return JSONResponse(status_code=400, content={"error": "Missing 'endpoint' field"})

    platform = getattr(request.state, "platform", "unknown")

    # If runtime not specified, auto-detect
    if not runtime:
        decision = decide_route(endpoint, payload)
        runtime = decision.get("runtime", "python")
        if not machine_type:
            machine_type = decision.get("machine_type", "e2-highmem-4")

    if not machine_type:
        machine_type = "e2-highmem-4"

    # Rate limit check
    rate_error = check_rate_limit(platform)
    if rate_error:
        return rate_error

    result = await submit_batch_job(
        platform=platform,
        endpoint=endpoint,
        payload=payload,
        runtime=runtime,
        machine_type=machine_type,
    )

    # Record metrics
    analysis = estimate_payload_size(payload)
    record_request(
        platform=platform,
        endpoint=endpoint,
        mode="async",
        n_points=analysis["n_points"],
        n_blocks=analysis["n_blocks"],
        error="error" in result and result.get("status") == "failed",
    )

    status_code = 202 if result.get("status") != "failed" else 500
    return JSONResponse(status_code=status_code, content=result)


@app.get("/api/v1/jobs/{job_id}/status")
async def job_status(job_id: str):
    """Poll job status."""
    return await get_job_status(job_id)


@app.get("/api/v1/jobs/{job_id}/result")
async def job_result(job_id: str):
    """Get job result (downloads from GCS)."""
    # First check status
    status = await get_job_status(job_id)
    if status.get("status") != "succeeded":
        return JSONResponse(
            status_code=409,
            content={"error": "Job not completed", "current_status": status.get("status"), "job_id": job_id}
        )

    result = await get_job_result(job_id)
    if not result:
        return JSONResponse(
            status_code=404,
            content={"error": "Result not found", "message": f"Output for job {job_id} not available on GCS"}
        )

    return result


# ============================================================
# Smart Proxy (sync — auto-route to Cloud Run or auto-submit to Batch)
# ============================================================

@app.api_route("/api/{path:path}", methods=["GET", "POST", "PUT", "DELETE"])
async def smart_proxy(request: Request, path: str):
    """
    Unified API endpoint.
    - If payload is small → proxies synchronously to Cloud Run.
    - If payload is large + ?mode=async → submits to Cloud Batch and returns job_id.
    - If payload is large (no mode param) → still proxies to Cloud Run (backward compat),
      but adds X-Route-Suggestion: async header.
    """
    platform = getattr(request.state, "platform", "unknown")

    # Rate limit
    rate_error = check_rate_limit(platform)
    if rate_error:
        return rate_error

    # Parse payload for analysis
    payload = {}
    if request.method in ("POST", "PUT", "PATCH"):
        try:
            payload = await request.json()
        except Exception:
            payload = {}

    # Route decision
    force_async = request.query_params.get("mode") == "async"
    decision = decide_route(path, payload)

    start_time = time.time()
    analysis = decision["analysis"]

    # Async mode (explicit or auto)
    if force_async or (decision["mode"] == "async" and request.query_params.get("mode") != "sync"):
        if not CLOUD_BATCH_AVAILABLE:
            # Fall through to sync if Cloud Batch unavailable
            logger.warning(f"Cloud Batch unavailable — falling back to sync for {path}")
        else:
            result = await submit_batch_job(
                platform=platform,
                endpoint=path,
                payload=payload,
                runtime=decision.get("runtime", "python"),
                machine_type=decision.get("machine_type", "e2-highmem-4"),
            )
            record_request(
                platform=platform, endpoint=path, mode="async",
                n_points=analysis["n_points"], n_blocks=analysis["n_blocks"],
            )
            return JSONResponse(status_code=202, content=result)

    # Sync mode — proxy to Cloud Run
    backend_url = resolve_backend(path)
    if not backend_url:
        return JSONResponse(
            status_code=404,
            content={"error": "Unknown endpoint", "path": f"/api/{path}", "available": list(resolve_backend.__code__.co_consts)}
        )

    # Forward to Cloud Run
    # Rebuild target path (strip common prefixes)
    target_path = f"/api/{path}"

    response = await proxy_to_cloud_run(request, backend_url, target_path)

    elapsed_ms = int((time.time() - start_time) * 1000)

    # Add routing metadata headers
    response.headers["X-Route-Mode"] = "sync"
    response.headers["X-Route-Runtime"] = decision.get("runtime", "python")
    response.headers["X-Gateway-Ms"] = str(elapsed_ms)

    # If workload was heavy but served sync (backward compat), suggest async
    if decision["mode"] == "async":
        response.headers["X-Route-Suggestion"] = "async"
        response.headers["X-Route-Reason"] = decision.get("reason", "")

    # Record metrics
    record_request(
        platform=platform, endpoint=path, mode="sync",
        n_points=analysis["n_points"], n_blocks=analysis["n_blocks"],
        compute_ms=elapsed_ms,
        error=response.status_code >= 400,
    )

    return response


# ============================================================
# Startup
# ============================================================

@app.on_event("startup")
async def startup():
    logger.info("=" * 60)
    logger.info("Solarius API Gateway v1.0.0")
    logger.info(f"Python Geostat: {settings.PYTHON_GEOSTAT_URL}")
    logger.info(f"Python Viz: {settings.PYTHON_VIZ_URL}")
    logger.info(f"Cloud Batch: {'ENABLED' if CLOUD_BATCH_AVAILABLE else 'DISABLED'}")
    logger.info(f"Routing thresholds: {settings.MAX_POINTS_CLOUD_RUN} pts / {settings.MAX_BLOCKS_CLOUD_RUN} blocks / {settings.MAX_REALIZATIONS_CLOUD_RUN} real.")
    logger.info(f"Platforms: {list(settings.get_api_keys().values())}")
    logger.info("=" * 60)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=settings.PORT)
