"""
POST /block-model — Block model estimation (wraps /kriging).
Same API contract as Julia version.
"""
import time
import logging
import numpy as np
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from src.routes.kriging import kriging

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/block-model")
async def block_model(request: Request):
    body = await request.json()
    gc = body["grid_config"]

    # Build grid from block model config
    grid_x = [(gc["origin_x"] + (i + 0.5) * gc["block_size_x"]) for i in range(int(gc["num_blocks_x"]))]
    grid_y = [(gc["origin_y"] + (i + 0.5) * gc["block_size_y"]) for i in range(int(gc["num_blocks_y"]))]
    grid_z = [(gc["origin_z"] + (i + 0.5) * gc["block_size_z"]) for i in range(int(gc["num_blocks_z"]))]

    # Build synthetic kriging request body
    kriging_body = {
        "data_x": body["data_x"],
        "data_y": body["data_y"],
        "data_z": body["data_z"],
        "data_values": body["data_values"],
        "grid_x": grid_x,
        "grid_y": grid_y,
        "grid_z": grid_z,
        "variogram": body["variogram"],
        "method": body.get("method", "ordinary"),
        "max_neighbors": body.get("max_neighbors", 12),
        "min_neighbors": body.get("min_neighbors", 4),
        "search_radius": body.get("search_radius", 500.0),
    }

    # Create a mock request object
    class MockRequest:
        async def json(self):
            return kriging_body

    return await kriging(MockRequest())
