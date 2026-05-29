"""
POST /api/monte-carlo — Financial Monte Carlo simulation.
API contract aligned with TypeScript MonteCarloJuliaRequest/Response types.
"""
import time
import logging
import numpy as np
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)
router = APIRouter()


def sample_distribution(dist_type: str, params: dict, size: int, rng) -> np.ndarray:
    """Sample from distribution specification."""
    if dist_type == "normal":
        return rng.normal(params.get("mean", 0), params.get("std", 1), size)
    elif dist_type == "lognormal":
        return rng.lognormal(params.get("mean", 0), params.get("sigma", 0.5), size)
    elif dist_type == "triangular":
        return rng.triangular(params.get("min", 0), params.get("mode", 0.5), params.get("max", 1), size)
    elif dist_type == "beta":
        return rng.beta(params.get("alpha", 2), params.get("beta", 5), size)
    elif dist_type == "uniform":
        return rng.uniform(params.get("min", 0), params.get("max", 1), size)
    else:
        return rng.normal(params.get("mean", 0), params.get("std", 1), size)


@router.post("/api/monte-carlo")
async def montecarlo(request: Request):
    t0 = time.time()
    body = await request.json()

    # TS MonteCarloJuliaRequest fields
    project = body["project"]
    fixed_costs = body.get("fixed_costs", {})
    uncertainties = body.get("uncertainties", [])
    tax_regime = body.get("tax_regime", {})
    sim_config = body.get("simulation", {})

    life_years = project["life_years"]
    production_annual = project["production_annual"]
    capex = project["capex"]
    discount_rate = project["discount_rate"]

    mining_cost_base = fixed_costs.get("mining_cost_base", 15.0)
    processing_cost_base = fixed_costs.get("processing_cost_base", 25.0)
    g_and_a = fixed_costs.get("g_and_a", 5.0)

    corporate_tax = tax_regime.get("corporate_tax", 0.30)
    royalty = tax_regime.get("royalty", 0.03)

    iterations = sim_config.get("iterations", 10000)
    seed = sim_config.get("seed", 42)
    iterations = min(iterations, 100000)  # Cap for safety

    logger.info(f"Monte Carlo: {iterations} iterations, {len(uncertainties)} uncertainties")

    rng = np.random.default_rng(seed)

    # Sample uncertain variables
    sampled = {}
    for u in uncertainties:
        sampled[u["variable"]] = sample_distribution(
            u["distribution"], u["params"], iterations, rng
        )

    # Compute NPV for each iteration
    npvs = np.zeros(iterations)
    irrs_approx = np.zeros(iterations)
    paybacks = np.full(iterations, float(life_years))

    for i in range(iterations):
        # Get sampled or base values
        price = sampled.get("commodity_price", np.full(iterations, 1500.0))[i]
        grade = sampled.get("grade", np.full(iterations, 2.0))[i]
        recovery = sampled.get("recovery", np.full(iterations, 0.92))[i]
        mining_cost = sampled.get("mining_cost", np.full(iterations, mining_cost_base))[i]
        processing_cost = sampled.get("processing_cost", np.full(iterations, processing_cost_base))[i]

        # Annual cash flows
        revenue = production_annual * grade * recovery * price / 31.1035  # oz to g conversion
        opex = production_annual * (mining_cost + processing_cost + g_and_a)
        gross_profit = revenue - opex
        royalty_cost = revenue * royalty
        taxable = gross_profit - royalty_cost
        tax = max(0, taxable * corporate_tax)
        net_cf = taxable - tax

        # NPV
        cf_stream = [-capex] + [net_cf] * life_years
        npv = sum(cf / (1 + discount_rate)**t for t, cf in enumerate(cf_stream))
        npvs[i] = npv

        # Approximate IRR using NPV interpolation
        if net_cf > 0:
            irrs_approx[i] = (net_cf / capex) * 100  # Simplified ROI as %
        else:
            irrs_approx[i] = -10.0

        # Payback period
        cumulative = -capex
        for yr in range(1, life_years + 1):
            cumulative += net_cf
            if cumulative >= 0:
                paybacks[i] = yr
                break

    # Sensitivity analysis (Pearson correlation with NPV)
    sensitivity = []
    for u in uncertainties:
        vals = sampled[u["variable"]]
        if np.std(vals) > 0 and np.std(npvs) > 0:
            pearson = float(np.corrcoef(vals, npvs)[0, 1])
            from scipy.stats import spearmanr
            spearman = float(spearmanr(vals, npvs).correlation)
            # NPV swing: difference between P10 and P90 of NPV when this var is at extremes
            low_mask = vals <= np.percentile(vals, 20)
            high_mask = vals >= np.percentile(vals, 80)
            swing = float(np.mean(npvs[high_mask]) - np.mean(npvs[low_mask])) if np.any(low_mask) and np.any(high_mask) else 0.0
        else:
            pearson, spearman, swing = 0.0, 0.0, 0.0

        sensitivity.append({
            "variable": u["variable"],
            "pearson_correlation": pearson,
            "spearman_correlation": spearman,
            "npv_swing": swing,
        })

    # Histograms
    npv_hist_counts, npv_hist_bins = np.histogram(npvs, bins=50)
    irr_hist_counts, irr_hist_bins = np.histogram(irrs_approx, bins=50)
    sorted_npvs = np.sort(npvs)
    probabilities = np.linspace(0, 1, len(sorted_npvs)).tolist()

    # Risk metrics
    var_95 = float(np.percentile(npvs, 5))  # 5th percentile = 95% VaR
    cvar_mask = npvs <= var_95
    cvar_95 = float(np.mean(npvs[cvar_mask])) if np.any(cvar_mask) else float(var_95)

    elapsed = round(time.time() - t0, 3)

    # Response matches TS MonteCarloJuliaResponse
    return JSONResponse({
        "status": "success",
        "npv": {
            "mean": float(np.mean(npvs)),
            "std": float(np.std(npvs)),
            "P10": float(np.percentile(npvs, 10)),
            "P50": float(np.percentile(npvs, 50)),
            "P90": float(np.percentile(npvs, 90)),
            "min": float(np.min(npvs)),
            "max": float(np.max(npvs)),
        },
        "irr": {
            "mean": float(np.mean(irrs_approx)),
            "P10": float(np.percentile(irrs_approx, 10)),
            "P50": float(np.percentile(irrs_approx, 50)),
            "P90": float(np.percentile(irrs_approx, 90)),
            "unit": "%",
        },
        "payback": {
            "mean": float(np.mean(paybacks)),
            "median": float(np.median(paybacks)),
            "unit": "years",
        },
        "risk": {
            "VaR_95": var_95,
            "CVaR_95": cvar_95,
            "probability_positive_npv": float(np.mean(npvs > 0)),
            "probability_loss": float(np.mean(npvs < 0)),
            "max_loss": float(np.min(npvs)),
        },
        "sensitivity": sensitivity,
        "visualization": {
            "npv_histogram": {
                "bins": npv_hist_bins.tolist(),
                "counts": npv_hist_counts.tolist(),
            },
            "npv_scurve": {
                "values": sorted_npvs.tolist(),
                "probabilities": probabilities,
            },
            "irr_histogram": {
                "bins": irr_hist_bins.tolist(),
                "counts": irr_hist_counts.tolist(),
            },
        },
        "performance": {
            "total_time_s": elapsed,
            "iterations_per_second": float(iterations / max(elapsed, 0.001)),
        },
    }, headers={"X-Process-Time": str(elapsed)})
