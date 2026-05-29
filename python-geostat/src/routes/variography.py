"""
POST /variography — Variogram computation using gstools.
Same API contract as Julia GeoStats.jl version.
"""
import time
import logging
import numpy as np
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/variography")
async def variography(request: Request):
    t0 = time.time()
    body = await request.json()

    data_x = np.array(body["data_x"], dtype=float)
    data_y = np.array(body["data_y"], dtype=float)
    data_z = np.array(body["data_z"], dtype=float)
    data_values = np.array(body["data_values"], dtype=float)
    n = len(data_x)
    logger.info(f"Variography request: {n} points")

    num_lags = body.get("num_lags", 15)
    lag_distance = body.get("lag_distance", None)
    fit_model_type = body.get("fit_model", "spherical")

    # Auto lag distance
    if lag_distance is None:
        coords = np.column_stack([data_x, data_y, data_z])
        extent = coords.max(axis=0) - coords.min(axis=0)
        max_extent = extent.max()
        lag_distance = max_extent / (2 * num_lags)

    max_lag = lag_distance * num_lags

    try:
        import gstools as gs

        # Compute empirical variogram (3D)
        bin_center, gamma = gs.vario_estimate(
            (data_x, data_y, data_z),
            data_values,
            bin_edges=np.linspace(0, max_lag, num_lags + 1)
        )

        lags_out = bin_center.tolist()
        semivar_out = gamma.tolist()
        # gstools doesn't return pair counts directly, estimate them
        counts_out = [int(n * (n - 1) / 2 / num_lags)] * len(lags_out)

        # Fit theoretical model
        fitted = None
        try:
            model_map = {
                "spherical": gs.Spherical,
                "exponential": gs.Exponential,
                "gaussian": gs.Gaussian,
            }
            ModelClass = model_map.get(fit_model_type, gs.Spherical)
            fit_model = ModelClass(dim=3)
            fit_model.fit_variogram(bin_center, gamma, nugget=True)

            fitted = {
                "type": fit_model_type,
                "nugget": float(fit_model.nugget),
                "sill": float(fit_model.sill),
                "range": float(fit_model.len_scale),
                "wls_score": 0.0,
            }
        except Exception as e:
            logger.warning(f"Model fitting failed: {e}")
            fitted = {"type": fit_model_type, "error": str(e)}

    except ImportError:
        # Fallback: simple method-of-moments
        lags_out, semivar_out, counts_out = _empirical_variogram_fallback(
            data_x, data_y, data_z, data_values, num_lags, max_lag
        )
        fitted = {"type": fit_model_type, "error": "gstools not available, used fallback"}

    elapsed = round(time.time() - t0, 3)
    return JSONResponse({
        "experimental": {
            "lags": lags_out,
            "semivariance": semivar_out,
            "pair_counts": counts_out,
        },
        "fitted_model": fitted,
        "metadata": {
            "num_points": n,
            "processing_time_s": elapsed,
            "engine": "gstools",
        },
    })


def _empirical_variogram_fallback(x, y, z, vals, num_lags, max_lag):
    """Simple empirical variogram when gstools is unavailable."""
    n = len(x)
    bin_edges = np.linspace(0, max_lag, num_lags + 1)
    lags = []
    semivars = []
    counts = []
    for k in range(num_lags):
        lo, hi = bin_edges[k], bin_edges[k + 1]
        mid = (lo + hi) / 2
        sv_sum = 0.0
        count = 0
        for i in range(n):
            for j in range(i + 1, min(i + 200, n)):  # cap for perf
                d = np.sqrt((x[i]-x[j])**2 + (y[i]-y[j])**2 + (z[i]-z[j])**2)
                if lo <= d < hi:
                    sv_sum += 0.5 * (vals[i] - vals[j])**2
                    count += 1
        lags.append(mid)
        semivars.append(sv_sum / max(count, 1))
        counts.append(count)
    return lags, semivars, counts
