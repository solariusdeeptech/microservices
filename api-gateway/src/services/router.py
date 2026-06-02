"""
Intelligent router — decides Cloud Run (sync) vs Cloud Batch (async)
based on payload size analysis.
"""
from loguru import logger
from src.config import settings

# Endpoint categories
LIGHT_ONLY = {"health", "spatial-continuity", "hybrid-clustering", "envelope-geometry", "deep-kriging"}
HEAVY_CAPABLE = {"variography", "kriging", "sgs", "montecarlo", "pit-optimize", "block-model", "blockmodel"}
ALWAYS_ASYNC = set()  # future: MPS on huge grids


def estimate_payload_size(payload: dict) -> dict:
    """
    Analyze the request payload to estimate computational complexity.
    Returns {n_points, n_blocks, n_realizations, estimated_seconds}.
    """
    n_points = 0
    n_blocks = 0
    n_realizations = 1

    # Count data points
    if "data_x" in payload:
        n_points = len(payload.get("data_x", []))
    elif "composites" in payload:
        composites = payload["composites"]
        n_points = len(composites) if isinstance(composites, list) else 0
    elif "data" in payload and isinstance(payload["data"], list):
        n_points = len(payload["data"])
    elif "x" in payload and isinstance(payload["x"], list):
        n_points = len(payload["x"])

    # Count blocks
    if "block_model" in payload:
        bm = payload["block_model"]
        if isinstance(bm, dict):
            nx = bm.get("num_x", bm.get("nx", 1))
            ny = bm.get("num_y", bm.get("ny", 1))
            nz = bm.get("num_z", bm.get("nz", 1))
            n_blocks = nx * ny * nz
    elif "blocks" in payload and isinstance(payload["blocks"], list):
        n_blocks = len(payload["blocks"])

    # Realizations (SGS, Monte Carlo)
    n_realizations = payload.get("n_realizations", payload.get("num_simulations", 1))

    # Rough time estimate (seconds)
    est_seconds = 0
    if n_points > 0:
        est_seconds += n_points * 0.001  # ~1ms per point for variography/kriging
    if n_blocks > 0:
        est_seconds += n_blocks * 0.002  # ~2ms per block for estimation
    est_seconds *= max(1, n_realizations * 0.5)

    return {
        "n_points": n_points,
        "n_blocks": n_blocks,
        "n_realizations": n_realizations,
        "estimated_seconds": round(est_seconds, 1),
    }


def decide_route(endpoint: str, payload: dict) -> dict:
    """
    Decide whether to route to Cloud Run (sync) or Cloud Batch (async).
    Returns {
        mode: 'sync' | 'async',
        runtime: 'python' | 'julia',
        reason: str,
        machine_type: str (for async only),
        analysis: dict
    }
    """
    # Normalize endpoint
    clean_ep = endpoint.strip("/").split("/")[0] if "/" in endpoint else endpoint.strip("/")

    # Always sync endpoints
    if clean_ep in LIGHT_ONLY:
        return {
            "mode": "sync",
            "runtime": "python",
            "reason": f"{clean_ep} is a lightweight endpoint — always Cloud Run",
            "analysis": estimate_payload_size(payload),
        }

    # Force async endpoints
    if clean_ep in ALWAYS_ASYNC:
        return {
            "mode": "async",
            "runtime": "julia",
            "reason": f"{clean_ep} always runs async on Cloud Batch",
            "machine_type": "e2-highmem-8",
            "analysis": estimate_payload_size(payload),
        }

    # Smart routing based on payload size
    analysis = estimate_payload_size(payload)
    n_points = analysis["n_points"]
    n_blocks = analysis["n_blocks"]
    n_real = analysis["n_realizations"]

    # Check thresholds
    is_heavy = (
        n_points > settings.MAX_POINTS_CLOUD_RUN
        or n_blocks > settings.MAX_BLOCKS_CLOUD_RUN
        or n_real > settings.MAX_REALIZATIONS_CLOUD_RUN
    )

    # Allow client to force async with ?mode=async query param
    # (handled at route level, not here)

    if is_heavy and clean_ep in HEAVY_CAPABLE:
        # Choose runtime : Julia for computation-heavy, Python for ML-heavy
        julia_endpoints = {"variography", "kriging", "sgs", "block-model", "blockmodel"}
        runtime = "julia" if clean_ep in julia_endpoints else "python"

        # Choose machine size based on scale
        if n_blocks > 1_000_000 or n_points > 500_000:
            machine = "e2-highmem-16"  # 16 vCPU, 128 GiB
        elif n_blocks > 200_000 or n_points > 100_000:
            machine = "e2-highmem-8"   # 8 vCPU, 64 GiB
        else:
            machine = "e2-highmem-4"   # 4 vCPU, 32 GiB

        reasons = []
        if n_points > settings.MAX_POINTS_CLOUD_RUN:
            reasons.append(f"{n_points} points > {settings.MAX_POINTS_CLOUD_RUN} threshold")
        if n_blocks > settings.MAX_BLOCKS_CLOUD_RUN:
            reasons.append(f"{n_blocks} blocks > {settings.MAX_BLOCKS_CLOUD_RUN} threshold")
        if n_real > settings.MAX_REALIZATIONS_CLOUD_RUN:
            reasons.append(f"{n_real} realizations > {settings.MAX_REALIZATIONS_CLOUD_RUN} threshold")

        return {
            "mode": "async",
            "runtime": runtime,
            "reason": f"Heavy workload: {', '.join(reasons)}",
            "machine_type": machine,
            "analysis": analysis,
        }

    # Default: sync on Cloud Run (Python)
    return {
        "mode": "sync",
        "runtime": "python",
        "reason": f"Workload within Cloud Run limits ({n_points} pts, {n_blocks} blocks, {n_real} realizations)",
        "analysis": analysis,
    }
