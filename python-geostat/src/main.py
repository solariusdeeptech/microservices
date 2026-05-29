"""
Solarius Microservices — Python Geostat API Server
Replaces Julia GeoStats.jl with gstools + pykrige + numpy/scipy.
Same endpoints, same API contracts.
"""
import os
import sys
import time
import logging
from datetime import datetime, timezone

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from src.middleware.auth import verify_api_key
from src.routes.variography import router as variography_router
from src.routes.kriging import router as kriging_router
from src.routes.sgs import router as sgs_router
from src.routes.montecarlo import router as montecarlo_router
from src.routes.pit_optimize import router as pit_router
from src.routes.block_model import router as block_model_router
from src.routes.ml_domaining import router as ml_domaining_router

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)

app = FastAPI(title="Solarius Python Geostat", version="2.0.0")

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Auth middleware
@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    if request.url.path == "/health" or request.method == "OPTIONS":
        return await call_next(request)
    error = verify_api_key(request)
    if error:
        return error
    return await call_next(request)

# Health
@app.get("/health")
async def health():
    return {
        "status": "healthy",
        "service": "python-geostat",
        "version": "2.0.0",
        "python_version": sys.version,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "ready": True,
        "capabilities": [
            "variography", "kriging", "sgs",
            "montecarlo", "pit_optimization", "block_model_estimation",
            "ml_domaining"
        ]
    }

# Routes
app.include_router(variography_router)
app.include_router(kriging_router)
app.include_router(sgs_router)
app.include_router(montecarlo_router)
app.include_router(pit_router)
app.include_router(block_model_router)
app.include_router(ml_domaining_router)

logger.info("Solarius Python Geostat API ready")
