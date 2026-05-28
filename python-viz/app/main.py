"""
Solarius Microservices — Python Viz API Server
"""

import os
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import ORJSONResponse
from loguru import logger

from app.routes import health, render_3d, mps, sections, drillholes
from app.middleware.auth import verify_api_key


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Solarius Python Viz API starting")
    os.environ["PYVISTA_OFF_SCREEN"] = "true"
    try:
        import pyvista as pv
        pv.OFF_SCREEN = True
        logger.info(f"VTK version: {pv.vtk_version}")
    except Exception as e:
        logger.warning(f"PyVista init warning (non-fatal): {e}")
    port = os.environ.get('PORT', '8080')
    logger.info(f"Listening on port {port}")
    yield
    logger.info("Shutting down...")


app = FastAPI(
    title="Solarius Python Viz Microservice",
    version="1.0.0",
    default_response_class=ORJSONResponse,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def log_requests(request: Request, call_next):
    start = time.time()
    response = await call_next(request)
    elapsed = round((time.time() - start) * 1000, 1)
    logger.info(f"{request.method} {request.url.path} -> {response.status_code} ({elapsed}ms)")
    return response


app.include_router(health.router, tags=["Health"])
app.include_router(render_3d.router, prefix="", tags=["3D Rendering"], dependencies=[Depends(verify_api_key)])
app.include_router(mps.router, prefix="", tags=["MPS"], dependencies=[Depends(verify_api_key)])
app.include_router(sections.router, prefix="", tags=["Sections"], dependencies=[Depends(verify_api_key)])
app.include_router(drillholes.router, prefix="", tags=["Drillholes"], dependencies=[Depends(verify_api_key)])
