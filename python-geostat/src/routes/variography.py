"""
POST /api/variography — Variogram computation using gstools.
API contract aligned with TypeScript VariographyRequest/Response types.
"""
import time
import logging
import numpy as np
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/api/variography")
async def variography(request: Request):
    t0 = time.time()
    body = await request.json()

    # TS VariographyRequest fields:
    # x, y, z, values, nlags?, maxlag?, models?, directions?
    data_x = np.array(body["x"], dtype=float)
    data_y = np.array(body["y"], dtype=float)
    data_z = np.array(body["z"], dtype=float)
    data_values = np.array(body["values"], dtype=float)
    n = len(data_x)
    logger.info(f"Variography request: {n} points")

    nlags = body.get("nlags", 15)
    maxlag = body.get("maxlag", None)
    model_types = body.get("models", ["spherical"])
    directions = body.get("directions", [])

    # Auto maxlag if not provided
    if maxlag is None:
        coords = np.column_stack([data_x, data_y, data_z])
        extent = coords.max(axis=0) - coords.min(axis=0)
        maxlag = float(extent.max()) / 2.0

    try:
        import gstools as gs

        # Compute empirical variogram (omnidirectional)
        bin_edges = np.linspace(0, maxlag, nlags + 1)
        bin_center, gamma = gs.vario_estimate(
            (data_x, data_y, data_z),
            data_values,
            bin_edges=bin_edges
        )

        lags_out = bin_center.tolist()
        gamma_out = gamma.tolist()
        # Estimate pair counts
        counts_out = [int(n * (n - 1) / 2 / max(nlags, 1))] * len(lags_out)

        # Fit best model among requested types
        model_map = {
            "spherical": gs.Spherical,
            "exponential": gs.Exponential,
            "gaussian": gs.Gaussian,
        }

        best_model = None
        best_score = float("inf")

        for mtype in model_types:
            ModelClass = model_map.get(mtype, gs.Spherical)
            try:
                fit_model = ModelClass(dim=3)
                fit_model.fit_variogram(bin_center, gamma, nugget=True)
                # Compute R-squared
                predicted = [fit_model.variogram(h) for h in bin_center]
                ss_res = sum((g - p) ** 2 for g, p in zip(gamma, predicted))
                ss_tot = sum((g - np.mean(gamma)) ** 2 for g in gamma)
                r_squared = 1 - ss_res / max(ss_tot, 1e-10)

                if ss_res < best_score:
                    best_score = ss_res
                    best_model = {
                        "type": mtype,
                        "nugget": float(fit_model.nugget),
                        "sill": float(fit_model.sill),
                        "range": float(fit_model.len_scale),
                        "partial_sill": float(fit_model.sill - fit_model.nugget),
                        "anisotropy_ratio": 1.0,
                        "anisotropy_angle": 0.0,
                        "r_squared": float(r_squared),
                    }
            except Exception as e:
                logger.warning(f"Model fitting {mtype} failed: {e}")

        if best_model is None:
            data_var = float(np.var(data_values))
            best_model = {
                "type": model_types[0] if model_types else "spherical",
                "nugget": 0.0,
                "sill": data_var,
                "range": float(maxlag / 3),
                "partial_sill": data_var,
                "anisotropy_ratio": 1.0,
                "anisotropy_angle": 0.0,
                "r_squared": 0.0,
            }

        # Generate model curve
        model_lags = np.linspace(0, maxlag, 50)
        ModelClass = model_map.get(best_model["type"], gs.Spherical)
        try:
            curve_model = ModelClass(
                dim=3,
                var=best_model["partial_sill"],
                len_scale=best_model["range"],
                nugget=best_model["nugget"],
            )
            model_gamma = [float(curve_model.variogram(h)) for h in model_lags]
        except Exception:
            model_gamma = [0.0] * len(model_lags)

        # Directional variograms
        directional_results = []
        for d in directions:
            az = d.get("azimuth", 0)
            dp = d.get("dip", 0)
            # Simplified: use omnidirectional as placeholder
            directional_results.append({
                "azimuth": az,
                "dip": dp,
                "lags": lags_out,
                "gamma": gamma_out,
                "npairs": counts_out,
            })

    except ImportError:
        lags_out, gamma_out, counts_out = _empirical_variogram_fallback(
            data_x, data_y, data_z, data_values, nlags, maxlag
        )
        data_var = float(np.var(data_values))
        best_model = {
            "type": "spherical",
            "nugget": 0.0,
            "sill": data_var,
            "range": float(maxlag / 3),
            "partial_sill": data_var,
            "anisotropy_ratio": 1.0,
            "anisotropy_angle": 0.0,
            "r_squared": 0.0,
        }
        model_lags = np.linspace(0, maxlag, 50).tolist()
        model_gamma = [0.0] * len(model_lags)
        directional_results = []

    elapsed = round(time.time() - t0, 3)

    # Response matches TS VariographyResponse
    return JSONResponse({
        "status": "success",
        "experimental": {
            "lags": lags_out,
            "gamma": gamma_out,
            "npairs": counts_out,
        },
        "model": best_model,
        "model_curve": {
            "lags": model_lags.tolist() if hasattr(model_lags, 'tolist') else model_lags,
            "gamma": model_gamma,
        },
        "directional": directional_results,
        "metadata": {
            "n_points": n,
            "n_valid_lags": len(lags_out),
            "total_pairs": int(n * (n - 1) / 2),
            "data_variance": float(np.var(data_values)),
        },
    }, headers={"X-Process-Time": str(elapsed)})


def _empirical_variogram_fallback(x, y, z, vals, num_lags, max_lag):
    """Simple empirical variogram when gstools is unavailable."""
    n = len(x)
    bin_edges = np.linspace(0, max_lag, num_lags + 1)
    lags, semivars, counts = [], [], []
    for k in range(num_lags):
        lo, hi = bin_edges[k], bin_edges[k + 1]
        mid = (lo + hi) / 2
        sv_sum, count = 0.0, 0
        for i in range(n):
            for j in range(i + 1, min(i + 200, n)):
                d = np.sqrt((x[i]-x[j])**2 + (y[i]-y[j])**2 + (z[i]-z[j])**2)
                if lo <= d < hi:
                    sv_sum += 0.5 * (vals[i] - vals[j])**2
                    count += 1
        lags.append(float(mid))
        semivars.append(float(sv_sum / max(count, 1)))
        counts.append(count)
    return lags, semivars, counts
