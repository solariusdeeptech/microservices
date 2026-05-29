"""
POST /montecarlo — Monte Carlo financial simulation.
Same API contract as Julia version.
"""
import time
import logging
import numpy as np
from scipy import stats as sp_stats
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/montecarlo")
async def montecarlo(request: Request):
    t0 = time.time()
    body = await request.json()

    econ = body["economics"]
    uncertainties = body["uncertainties"]
    sim_config = body["simulation"]
    iterations = sim_config.get("iterations", 10000)
    seed = sim_config.get("seed", 42)

    logger.info(f"Monte Carlo request: {iterations} iterations")
    rng = np.random.default_rng(seed)

    # Build distributions
    distributions = {}
    for u in uncertainties:
        var_name = u["variable"]
        dist_type = u["distribution"]
        params = u["params"]

        if dist_type == "triangular":
            lo, hi, mode = params["min"], params["max"], params["mode"]
            c = (mode - lo) / (hi - lo)
            distributions[var_name] = ("triang", {"c": c, "loc": lo, "scale": hi - lo})
        elif dist_type == "normal":
            distributions[var_name] = ("norm", {"loc": params["mean"], "scale": params["std"]})
        elif dist_type == "lognormal":
            distributions[var_name] = ("lognorm", {"s": params["sdlog"], "scale": np.exp(params["meanlog"])})
        elif dist_type == "uniform":
            distributions[var_name] = ("uniform", {"loc": params["min"], "scale": params["max"] - params["min"]})
        else:
            distributions[var_name] = ("norm", {"loc": params.get("mean", 0), "scale": params.get("std", 1)})

    # Base parameters
    base_lom = int(econ.get("lom_years", 10))
    base_tonnage = float(econ.get("tonnage_mtpa", 5.0))
    base_grade = float(econ.get("grade", 2.5))
    base_recovery = float(econ.get("recovery", 0.92))
    base_price = float(econ.get("metal_price", 1800.0))
    base_opex = float(econ.get("opex", 50.0))
    base_capex = float(econ.get("capex", 200.0))
    discount_rate = float(econ.get("discount_rate", 0.08))

    # Sample all at once (vectorized)
    samples = {}
    for var_name, (dist_name, params) in distributions.items():
        dist = getattr(sp_stats, dist_name)
        samples[var_name] = dist.rvs(**params, size=iterations, random_state=rng)

    prices = samples.get("goldPrice", np.full(iterations, base_price))
    grades = samples.get("grade", np.full(iterations, base_grade))
    capexs = samples.get("capex", np.full(iterations, base_capex))
    opexs = samples.get("opex", np.full(iterations, base_opex))

    # Vectorized NPV calculation
    annual_revenue = base_tonnage * 1e6 * grades / 1e6 * base_recovery * prices
    annual_cost = base_tonnage * 1e6 * opexs / 1e6
    annual_cf = annual_revenue - annual_cost

    discount_factors = np.sum([1.0 / (1 + discount_rate)**t for t in range(1, base_lom + 1)])
    npvs = -capexs + annual_cf * discount_factors

    # IRR (vectorized approximation)
    irrs = np.array([_compute_irr(-capexs[i], annual_cf[i], base_lom) for i in range(iterations)])

    # Statistics
    percentiles = {
        "p5": float(np.percentile(npvs, 5)),
        "p10": float(np.percentile(npvs, 10)),
        "p25": float(np.percentile(npvs, 25)),
        "p50": float(np.percentile(npvs, 50)),
        "p75": float(np.percentile(npvs, 75)),
        "p90": float(np.percentile(npvs, 90)),
        "p95": float(np.percentile(npvs, 95)),
    }

    prob_positive = float(np.mean(npvs > 0))

    # Histogram
    hist_counts, hist_edges = np.histogram(npvs, bins=50)
    histogram = [
        {
            "bin_start": float(hist_edges[i]),
            "bin_end": float(hist_edges[i + 1]),
            "count": int(hist_counts[i]),
            "frequency": float(hist_counts[i] / iterations),
        }
        for i in range(len(hist_counts))
    ]

    elapsed = round(time.time() - t0, 3)
    return JSONResponse({
        "npv_statistics": {
            "mean": float(np.mean(npvs)),
            "std": float(np.std(npvs)),
            "min": float(np.min(npvs)),
            "max": float(np.max(npvs)),
            "percentiles": percentiles,
            "probability_positive": prob_positive,
        },
        "irr_statistics": {
            "mean": float(np.mean(irrs)),
            "std": float(np.std(irrs)),
            "p10": float(np.percentile(irrs, 10)),
            "p50": float(np.percentile(irrs, 50)),
            "p90": float(np.percentile(irrs, 90)),
        },
        "histogram": histogram,
        "metadata": {
            "iterations": iterations,
            "processing_time_s": elapsed,
            "engine": "numpy/scipy",
        },
    })


def _compute_irr(initial, annual_cf, years, max_iter=100):
    r = 0.10
    for _ in range(max_iter):
        npv = initial
        dnpv = 0.0
        for t in range(1, years + 1):
            npv += annual_cf / (1 + r)**t
            dnpv -= t * annual_cf / (1 + r)**(t + 1)
        if abs(dnpv) < 1e-12:
            break
        r -= npv / dnpv
        r = max(-0.95, min(r, 5.0))
        if abs(npv) < 1e-6:
            break
    return r
