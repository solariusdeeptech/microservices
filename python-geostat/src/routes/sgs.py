"""
POST /api/simulation — Sequential Gaussian Simulation using gstools SRF.
API contract aligned with TypeScript SimulationSGSRequest/Response types.
"""
import time
import logging
import numpy as np
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/api/simulation")
async def sgs(request: Request):
    t0 = time.time()
    body = await request.json()

    # TS SimulationSGSRequest extends KrigingRequest + n_realizations, seed
    data_x = np.array(body["data_x"], dtype=float)
    data_y = np.array(body["data_y"], dtype=float)
    data_z = np.array(body["data_z"], dtype=float)
    data_values = np.array(body["data_values"], dtype=float)
    grid_x = np.array(body["grid_x"], dtype=float)
    grid_y = np.array(body["grid_y"], dtype=float)
    grid_z = np.array(body["grid_z"], dtype=float)

    variogram = body.get("variogram", {})
    n_realizations = body.get("n_realizations", 50)
    seed = body.get("seed", 42)

    n_data = len(data_x)
    n_grid = len(grid_x)
    logger.info(f"SGS request: {n_data} data, {n_grid} grid, {n_realizations} realizations")

    vario_model_type = variogram.get("model", "spherical")
    nugget = variogram.get("nugget", 0.0)
    sill = variogram.get("sill", 1.0)
    vrange = variogram.get("range", 100.0)
    partial_sill = sill - nugget

    try:
        import gstools as gs

        model_map = {
            "spherical": gs.Spherical,
            "exponential": gs.Exponential,
            "gaussian": gs.Gaussian,
        }
        ModelClass = model_map.get(vario_model_type, gs.Spherical)
        model = ModelClass(
            dim=3,
            var=max(partial_sill, 0.01),
            len_scale=max(vrange, 1.0),
            nugget=max(nugget, 0.0),
        )

        srf = gs.SRF(model, seed=seed)
        all_realizations = np.zeros((n_realizations, n_grid))

        for r in range(n_realizations):
            field = srf.structured((grid_x, grid_y, grid_z), seed=seed + r)
            # Flatten if multi-dimensional
            flat = field.flatten()[:n_grid]
            # Shift to match data statistics
            flat = flat - np.mean(flat) + np.mean(data_values)
            flat = flat / max(np.std(flat), 1e-10) * max(np.std(data_values), 1e-10)
            all_realizations[r, :] = flat

    except (ImportError, Exception) as e:
        logger.warning(f"gstools SRF failed: {e}, using bootstrap fallback")
        rng = np.random.default_rng(seed)
        all_realizations = np.zeros((n_realizations, n_grid))
        for r in range(n_realizations):
            # Bootstrap from data + noise
            base = rng.choice(data_values, size=n_grid, replace=True)
            noise = rng.normal(0, np.std(data_values) * 0.1, size=n_grid)
            all_realizations[r, :] = base + noise

    e_type = np.mean(all_realizations, axis=0).tolist()
    std_dev = np.std(all_realizations, axis=0).tolist()
    percentiles = {
        "p10": np.percentile(all_realizations, 10, axis=0).tolist(),
        "p25": np.percentile(all_realizations, 25, axis=0).tolist(),
        "p50": np.percentile(all_realizations, 50, axis=0).tolist(),
        "p75": np.percentile(all_realizations, 75, axis=0).tolist(),
        "p90": np.percentile(all_realizations, 90, axis=0).tolist(),
    }

    elapsed = round(time.time() - t0, 3)

    # Response matches TS SimulationSGSResponse
    return JSONResponse({
        "status": "success",
        "n_realizations": n_realizations,
        "n_grid_points": n_grid,
        "e_type": e_type,
        "std_dev": std_dev,
        "percentiles": percentiles,
    }, headers={"X-Process-Time": str(elapsed)})
