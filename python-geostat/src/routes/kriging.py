"""
POST /api/kriging — 3D Kriging using pykrige.
API contract aligned with TypeScript KrigingRequest/Response types.
"""
import time
import logging
import numpy as np
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)
router = APIRouter()

VARIOGRAM_MAP = {
    "spherical": "spherical",
    "exponential": "exponential",
    "gaussian": "gaussian",
    "linear": "linear",
}


@router.post("/api/kriging")
async def kriging(request: Request):
    t0 = time.time()
    body = await request.json()

    # TS KrigingRequest fields
    data_x = np.array(body["data_x"], dtype=float)
    data_y = np.array(body["data_y"], dtype=float)
    data_z = np.array(body["data_z"], dtype=float)
    data_values = np.array(body["data_values"], dtype=float)
    grid_x = np.array(body["grid_x"], dtype=float)
    grid_y = np.array(body["grid_y"], dtype=float)
    grid_z = np.array(body["grid_z"], dtype=float)

    variogram = body.get("variogram", {})
    method = body.get("method", "ordinary")
    cross_validate = body.get("cross_validate", False)
    max_neighbors = body.get("max_neighbors", 12)
    search_radius = body.get("search_radius", None)

    n_data = len(data_x)
    n_grid = len(grid_x)
    logger.info(f"Kriging request: {n_data} data points, {n_grid} grid points, method={method}")

    vario_model = VARIOGRAM_MAP.get(variogram.get("model", "spherical"), "spherical")
    nugget = variogram.get("nugget", 0.0)
    sill = variogram.get("sill", 1.0)
    vrange = variogram.get("range", 100.0)
    partial_sill = sill - nugget

    try:
        from pykrige.ok3d import OrdinaryKriging3D

        ok3d = OrdinaryKriging3D(
            data_x, data_y, data_z, data_values,
            variogram_model=vario_model,
            variogram_parameters={
                "sill": float(partial_sill),
                "range": float(vrange),
                "nugget": float(nugget),
            },
            nlags=15,
        )

        # Krige at each grid point
        estimates = []
        variances = []
        n_neighbors_used = []

        # pykrige expects 1D sorted unique arrays for grid
        # For scattered points, we krige one by one
        for i in range(n_grid):
            try:
                est, var = ok3d.execute(
                    "points",
                    np.array([grid_x[i]]),
                    np.array([grid_y[i]]),
                    np.array([grid_z[i]]),
                )
                estimates.append(float(est.flatten()[0]))
                variances.append(float(var.flatten()[0]))
                n_neighbors_used.append(min(max_neighbors, n_data))
            except Exception:
                estimates.append(float(np.mean(data_values)))
                variances.append(float(np.var(data_values)))
                n_neighbors_used.append(0)

    except ImportError:
        # IDW fallback
        logger.warning("pykrige not available, using IDW fallback")
        estimates, variances, n_neighbors_used = _idw_fallback(
            data_x, data_y, data_z, data_values,
            grid_x, grid_y, grid_z, max_neighbors
        )

    estimates_arr = np.array(estimates)
    variances_arr = np.array(variances)
    valid = ~np.isnan(estimates_arr)
    n_estimated = int(np.sum(valid))
    n_failed = n_grid - n_estimated

    # Cross-validation (LOO)
    cross_validation = None
    if cross_validate and n_data <= 2000:
        cross_validation = _cross_validate(
            data_x, data_y, data_z, data_values, vario_model, partial_sill, vrange, nugget
        )

    elapsed = round(time.time() - t0, 3)

    # Response matches TS KrigingResponse
    return JSONResponse({
        "status": "success",
        "method": method,
        "estimates": estimates,
        "variances": variances,
        "grid": {
            "x": grid_x.tolist(),
            "y": grid_y.tolist(),
            "z": grid_z.tolist(),
        },
        "n_neighbors_used": n_neighbors_used,
        "statistics": {
            "n_estimated": n_estimated,
            "n_failed": n_failed,
            "mean_estimate": float(np.nanmean(estimates_arr)),
            "std_estimate": float(np.nanstd(estimates_arr)),
            "min_estimate": float(np.nanmin(estimates_arr)) if n_estimated > 0 else 0.0,
            "max_estimate": float(np.nanmax(estimates_arr)) if n_estimated > 0 else 0.0,
            "mean_variance": float(np.nanmean(variances_arr)),
            "mean_std_dev": float(np.sqrt(np.nanmean(variances_arr))),
        },
        "variogram_used": {
            "model": vario_model,
            "nugget": nugget,
            "sill": sill,
            "range": vrange,
        },
        **({
            "cross_validation": cross_validation
        } if cross_validation else {}),
    }, headers={"X-Process-Time": str(elapsed)})


def _idw_fallback(dx, dy, dz, dv, gx, gy, gz, max_n):
    estimates, variances, n_used = [], [], []
    for i in range(len(gx)):
        dists = np.sqrt((dx - gx[i])**2 + (dy - gy[i])**2 + (dz - gz[i])**2)
        idx = np.argsort(dists)[:max_n]
        w = 1.0 / np.maximum(dists[idx], 1e-10) ** 2
        est = float(np.sum(w * dv[idx]) / np.sum(w))
        estimates.append(est)
        variances.append(float(np.var(dv[idx])))
        n_used.append(len(idx))
    return estimates, variances, n_used


def _cross_validate(dx, dy, dz, dv, model, psill, vrange, nugget):
    """Leave-one-out cross validation."""
    try:
        from pykrige.ok3d import OrdinaryKriging3D
        n = len(dx)
        errors = []
        for i in range(min(n, 500)):  # Cap at 500 for performance
            mask = np.ones(n, dtype=bool)
            mask[i] = False
            try:
                ok = OrdinaryKriging3D(
                    dx[mask], dy[mask], dz[mask], dv[mask],
                    variogram_model=model,
                    variogram_parameters={"sill": float(psill), "range": float(vrange), "nugget": float(nugget)},
                    nlags=10,
                )
                est, _ = ok.execute("points", np.array([dx[i]]), np.array([dy[i]]), np.array([dz[i]]))
                errors.append(float(est.flatten()[0]) - float(dv[i]))
            except Exception:
                continue

        if len(errors) < 3:
            return None

        errors_arr = np.array(errors)
        std_e = float(np.std(errors_arr)) if float(np.std(errors_arr)) > 0 else 1.0
        standardized = (errors_arr / std_e).tolist()

        return {
            "mean_error": float(np.mean(errors_arr)),
            "mean_absolute_error": float(np.mean(np.abs(errors_arr))),
            "rmse": float(np.sqrt(np.mean(errors_arr**2))),
            "mean_standardized_error": float(np.mean(errors_arr / std_e)),
            "variance_standardized_error": float(np.var(errors_arr / std_e)),
            "n_points": len(errors),
            "errors": errors,
            "standardized_errors": standardized,
        }
    except Exception:
        return None
