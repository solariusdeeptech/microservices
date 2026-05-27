"""
Solarius Microservices — Python Viz API Server

Endpoints:
  GET  /health       → Health check
  POST /render-3d    → Rendu 3D block model (glTF/image export)
  POST /mps          → Simulation Multi-Points (MPS)
  POST /sections     → Génération de coupes géologiques
  POST /drillholes   → Visualisation 3D de sondages
"""

import os
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import ORJSONResponse
from loguru import logger

from app.routes import health, render_3d, mps, sections, drillholes
from app.middleware.auth import verify_api_key


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup/shutdown events."""
    logger.info("🚀 Solarius Python Viz API démarré")
    logger.info(f"PyVista offscreen: {os.environ.get('PYVISTA_OFF_SCREEN', 'true')}")

    # Set PyVista to offscreen mode (CPU rendering via OSMesa)
    os.environ["PYVISTA_OFF_SCREEN"] = "true"
    import pyvista as pv
    pv.OFF_SCREEN = True

    logger.info(f"VTK version: {pv.vtk_version}")
    logger.info("Endpoints: /health, /render-3d, /mps, /sections, /drillholes")
    yield
    logger.info("Shutting down...")


app = FastAPI(
    title="Solarius Python Viz Microservice",
    version="1.0.0",
    default_response_class=ORJSONResponse,
    lifespan=lifespan,
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Request logging middleware
@app.middleware("http")
async def log_requests(request: Request, call_next):
    start = time.time()
    response = await call_next(request)
    elapsed = round((time.time() - start) * 1000, 1)
    logger.info(f"{request.method} {request.url.path} → {response.status_code} ({elapsed}ms)")
    return response


# Register routes
app.include_router(health.router, tags=["Health"])
app.include_router(render_3d.router, prefix="", tags=["3D Rendering"], dependencies=[Depends(verify_api_key)])
app.include_router(mps.router, prefix="", tags=["MPS"], dependencies=[Depends(verify_api_key)])
app.include_router(sections.router, prefix="", tags=["Sections"], dependencies=[Depends(verify_api_key)])
app.include_router(drillholes.router, prefix="", tags=["Drillholes"], dependencies=[Depends(verify_api_key)])
