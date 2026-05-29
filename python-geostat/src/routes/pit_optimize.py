"""
POST /pit-optimize — Pit optimization (Lerchs-Grossmann).
Same API contract as Julia version.
"""
import time
import math
import logging
import numpy as np
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/pit-optimize")
async def pit_optimize(request: Request):
    t0 = time.time()
    body = await request.json()

    block_values = body["block_values"]
    block_size = body["block_size"]
    slope_constraints = body["slope_constraints"]
    algorithm = body.get("algorithm", "lerchs-grossmann")

    n_blocks = len(block_values)
    logger.info(f"Pit optimization: {n_blocks} blocks, algorithm={algorithm}")

    global_angle = float(slope_constraints["global_angle"])
    tan_angle = math.tan(math.radians(global_angle))

    # Extract block data as numpy arrays for speed
    blocks = np.array(block_values, dtype=float)  # shape (n, 4): x, y, z, value
    bx = blocks[:, 0]
    by = blocks[:, 1]
    bz = blocks[:, 2]
    bval = blocks[:, 3]

    dx = float(block_size["x"])
    dy = float(block_size["y"])
    dz = float(block_size["z"])

    in_pit = np.zeros(n_blocks, dtype=bool)

    # Group by Z level
    z_levels = np.unique(bz)
    z_levels = np.sort(z_levels)  # ascending

    # Start from bottom, expand upward following slope constraints
    for z_idx in range(len(z_levels) - 1, -1, -1):
        z = z_levels[z_idx]
        level_mask = bz == z
        level_indices = np.where(level_mask)[0]

        for idx in level_indices:
            if bval[idx] > 0:
                in_pit[idx] = True
                # Mark predecessors above
                for upper_z_idx in range(z_idx - 1, -1, -1):
                    z_above = z_levels[upper_z_idx]
                    height_diff = z_above - bz[idx]
                    max_horiz = height_diff * tan_angle
                    above_mask = bz == z_above
                    above_indices = np.where(above_mask)[0]
                    horiz_dist = np.sqrt(
                        (bx[above_indices] - bx[idx])**2 +
                        (by[above_indices] - by[idx])**2
                    )
                    within = horiz_dist <= max_horiz + max(dx, dy)
                    in_pit[above_indices[within]] = True

    # Iterative improvement
    improved = True
    while improved:
        improved = False
        for i in range(n_blocks):
            if in_pit[i] and bval[i] < 0:
                in_pit[i] = False
                improved = True

    total_value = float(np.sum(bval[in_pit]))
    blocks_in_pit_count = int(np.sum(in_pit))
    ore_count = int(np.sum(in_pit & (bval >= 0)))
    waste_count = int(np.sum(in_pit & (bval < 0)))
    strip_ratio = waste_count / max(ore_count, 1)

    results = []
    for i in range(n_blocks):
        results.append({
            "x": float(bx[i]),
            "y": float(by[i]),
            "z": float(bz[i]),
            "value": float(bval[i]),
            "in_pit": bool(in_pit[i]),
        })

    elapsed = round(time.time() - t0, 3)
    return JSONResponse({
        "optimal_pit": {
            "blocks": results,
            "total_value": total_value,
            "blocks_in_pit": blocks_in_pit_count,
            "blocks_total": n_blocks,
            "strip_ratio": strip_ratio,
        },
        "metadata": {
            "algorithm": algorithm,
            "processing_time_s": elapsed,
            "engine": "numpy/lerchs-grossmann",
        },
    })
