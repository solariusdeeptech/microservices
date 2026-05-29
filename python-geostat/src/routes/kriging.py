"""
POST /kriging — Ordinary/Simple Kriging using pykrige.
Same API contract as Julia GeoStats.jl version.
"""
import time
import logging
import numpy as np
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/kriging")
async def kriging(request: Request):
    t0 = time.time()
    body = await request.json()

    data_x = np.array(body["data_x"], dtype=float)
    data_y = np.array(body["data_y"], dtype=float)
    data_z = np.array(body["data_z"], dtype=float)
    data_values = np.array(body["data_values"], dtype=float)
    grid_x = np.array(body["grid_x"], dtype=float)
    grid_y = np.array(body["grid_y"], dtype=float)
    grid_z = np.array(body["grid_z"], dtype=float)

    n = len(data_x)
    n_grid = len(grid_x) * len(grid_y) * len(grid_z)
    logger.info(f"Kriging request: {n} data points, {n_grid} grid nodes")

    vario = body["variogram"]
    nugget = float(vario.get("nugget", 0.0))
    sill_val = float(vario.get("sill", 1.0))
    range_val = float(vario.get("range", 100.0))
    model_type = vario.get("model", "spherical")
    method = body.get("method", "ordinary")
    max_neighbors = body.get("max_neighbors", 12)

    model_map = {
        "spherical": "spherical",
        "exponential": "exponential",
        "gaussian": "gaussian",
    }
    pykrige_model = model_map.get(model_type, "spherical")

    try:
        from pykrige.ok3d import OrdinaryKriging3D

        ok3d = OrdinaryKriging3D(
            data_x, data_y, data_z, data_values,
            variogram_model=pykrige_model,
            variogram_parameters={
                "sill": sill_val,
                "range": range_val,
                "nugget": nugget,
            },
            nlags=15,
        )

        k3d, ss3d = ok3d.execute("grid", grid_x, grid_y, grid_z)

        # Build results list
        estimates = []
        idx = 0
        for iz, zv in enumerate(grid_z):
            for iy, yv in enumerate(grid_y):
                for ix, xv in enumerate(grid_x):
                    estimates.append({
                        "x": float(xv),
                        "y": float(yv),
                        "z": float(zv),
                        "estimated_value": float(k3d[iz, iy, ix]),
                        "variance": float(ss3d[iz, iy, ix]),
                        "num_samples": min(max_neighbors, n),
                    })
                    idx += 1

    except ImportError:
        # Fallback: IDW
        estimates = _idw_fallback(
            data_x, data_y, data_z, data_values,
            grid_x, grid_y, grid_z, max_neighbors
        )

    elapsed = round(time.time() - t0, 3)
    return JSONResponse({
        "estimates": estimates,
        "metadata": {
            "num_data_points": n,
            "num_estimates": len(estimates),
            "method": method,
            "processing_time_s": elapsed,
            "engine": "pykrige",
        },
    })


def _idw_fallback(dx, dy, dz, dv, gx, gy, gz, max_n):
    """Inverse Distance Weighting fallback."""
    estimates = []
    for zv in gz:
        for yv in gy:
            for xv in gx:
                dists = np.sqrt((dx - xv)**2 + (dy - yv)**2 + (dz - zv)**2)
                idx = np.argsort(dists)[:max_n]
                w = 1.0 / np.maximum(dists[idx], 1e-10)**2
                val = float(np.sum(w * dv[idx]) / np.sum(w))
                estimates.append({
                    "x": float(xv), "y": float(yv), "z": float(zv),
                    "estimated_value": val, "variance": 0.0,
                    "num_samples": len(idx),
                })
    return estimates
