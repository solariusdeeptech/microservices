"""
POST /api/deep-kriging — DeepKriging™ RBF Interpolation Engine
High-performance block model estimation using Radial Basis Function
interpolation with adaptive kernels via scipy.interpolate.RBFInterpolator.

Significantly faster than the JS engine (scipy C/Fortran backend vs JS LU).
Supports: gaussian, multiquadric, inverse_multiquadric, thin_plate_spline, cubic, linear.
"""
import time
import logging
import numpy as np
from typing import Optional
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

try:
    from scipy.interpolate import RBFInterpolator
    from scipy.spatial import cKDTree
    HAS_SCIPY_RBF = True
except ImportError:
    HAS_SCIPY_RBF = False

logger = logging.getLogger(__name__)
router = APIRouter()

# Map user-friendly kernel names to scipy RBFInterpolator kernel names
KERNEL_MAP = {
    "gaussian": "gaussian",
    "multiquadric": "multiquadric",
    "inverse_multiquadric": "inverse_multiquadric",
    "inverse-multiquadric": "inverse_multiquadric",
    "thin_plate": "thin_plate_spline",
    "thin-plate": "thin_plate_spline",
    "thin_plate_spline": "thin_plate_spline",
    "cubic": "cubic",
    "linear": "linear",
}


def _auto_epsilon(points: np.ndarray, k: int = 5) -> float:
    """Auto-calibrate epsilon from average k-NN distance."""
    n = min(len(points), 200)
    tree = cKDTree(points[:n])
    dists, _ = tree.query(points[:n], k=min(k + 1, n))
    avg_nn = np.mean(dists[:, 1:])  # exclude self
    return 1.5 / max(avg_nn, 1e-6)


def _build_block_grid(
    origin_x: float, origin_y: float, origin_z: float,
    block_size_x: float, block_size_y: float, block_size_z: float,
    num_x: int, num_y: int, num_z: int
) -> tuple[np.ndarray, np.ndarray]:
    """Build block center coordinates and index arrays."""
    ix = np.arange(num_x)
    iy = np.arange(num_y)
    iz = np.arange(num_z)
    gx, gy, gz = np.meshgrid(ix, iy, iz, indexing="ij")
    gx, gy, gz = gx.ravel(), gy.ravel(), gz.ravel()

    cx = origin_x + (gx + 0.5) * block_size_x
    cy = origin_y + (gy + 0.5) * block_size_y
    cz = origin_z + (gz + 0.5) * block_size_z

    coords = np.column_stack([cx, cy, cz])
    indices = np.column_stack([gx, gy, gz]).astype(int)
    return coords, indices


def _deep_kriging_rbf(
    data_xyz: np.ndarray,       # (N, 3) sample coords
    data_values: np.ndarray,    # (N,)   sample grades
    grid_xyz: np.ndarray,       # (M, 3) block centers
    kernel: str = "gaussian",
    epsilon: float = 0.0,
    smoothing: float = 0.01,
    max_neighbors: int = 24,
    search_radius: float = 0.0,
    adaptive_epsilon: bool = True,
) -> tuple[np.ndarray, np.ndarray, dict]:
    """
    Core DeepKriging RBF estimation.

    Returns: (estimates, variances, meta)
    """
    n_samples = len(data_xyz)
    n_blocks = len(grid_xyz)
    scipy_kernel = KERNEL_MAP.get(kernel, "gaussian")

    # --- Auto epsilon ---
    if epsilon <= 0:
        epsilon = _auto_epsilon(data_xyz)

    # --- Build KD-Tree for fast neighbor search ---
    tree = cKDTree(data_xyz)

    # Determine effective search radius
    if search_radius <= 0:
        # Use 3× the average nearest-neighbor distance
        k_nn = min(6, n_samples)
        nn_dists, _ = tree.query(data_xyz, k=k_nn)
        search_radius = float(np.mean(nn_dists[:, 1:]) * 6)

    estimates = np.full(n_blocks, np.nan)
    variances = np.full(n_blocks, np.nan)
    num_neighbors_used = np.zeros(n_blocks, dtype=int)

    # --- Process blocks in spatial batches for cache efficiency ---
    BATCH = 2000
    for start in range(0, n_blocks, BATCH):
        end = min(start + BATCH, n_blocks)
        batch_xyz = grid_xyz[start:end]

        # Query neighbors for each block in batch
        # ball_point returns variable-length lists
        neighbor_lists = tree.query_ball_point(batch_xyz, r=search_radius)

        for i_local, nb_idx in enumerate(neighbor_lists):
            i_global = start + i_local
            nb_idx = np.array(nb_idx, dtype=int)

            if len(nb_idx) < 3:
                continue

            # Limit to max_neighbors (closest)
            if len(nb_idx) > max_neighbors:
                dists = np.linalg.norm(
                    data_xyz[nb_idx] - batch_xyz[i_local], axis=1
                )
                keep = np.argsort(dists)[:max_neighbors]
                nb_idx = nb_idx[keep]
                dists = dists[keep]
            else:
                dists = np.linalg.norm(
                    data_xyz[nb_idx] - batch_xyz[i_local], axis=1
                )

            local_pts = data_xyz[nb_idx]
            local_vals = data_values[nb_idx]

            # Adaptive epsilon per neighborhood
            if adaptive_epsilon and len(dists) > 1:
                local_eps = 1.0 / (np.mean(dists) + 0.1)
            else:
                local_eps = epsilon

            try:
                rbf = RBFInterpolator(
                    local_pts,
                    local_vals,
                    kernel=scipy_kernel,
                    epsilon=local_eps,
                    smoothing=smoothing,
                )
                est = rbf(batch_xyz[i_local:i_local + 1])
                estimates[i_global] = float(est[0])
                num_neighbors_used[i_global] = len(nb_idx)

                # Approximate variance from distance to nearest sample
                min_dist = float(np.min(dists))
                if scipy_kernel == "gaussian":
                    variances[i_global] = max(
                        0, 1.0 - np.exp(-(local_eps * min_dist) ** 2)
                    )
                else:
                    variances[i_global] = max(
                        0, min_dist / (search_radius + 1e-6)
                    )
            except Exception as e:
                logger.debug(f"RBF failed for block {i_global}: {e}")
                continue

    # --- Clamp negative grades (physical constraint) ---
    valid = ~np.isnan(estimates)
    estimates[valid] = np.clip(estimates[valid], 0, None)

    # --- Statistics ---
    valid_est = estimates[valid]
    meta = {
        "epsilon_used": float(epsilon),
        "search_radius": float(search_radius),
        "kernel": scipy_kernel,
        "n_samples": int(n_samples),
        "total_blocks": int(n_blocks),
        "estimated_blocks": int(np.sum(valid)),
        "skipped_blocks": int(n_blocks - np.sum(valid)),
        "mean_neighbors": float(np.mean(num_neighbors_used[valid])) if np.any(valid) else 0,
    }
    if len(valid_est) > 0:
        meta["stats"] = {
            "mean": float(np.mean(valid_est)),
            "std": float(np.std(valid_est)),
            "min": float(np.min(valid_est)),
            "max": float(np.max(valid_est)),
            "median": float(np.median(valid_est)),
            "p10": float(np.percentile(valid_est, 10)),
            "p90": float(np.percentile(valid_est, 90)),
        }

    return estimates, variances, meta


@router.post("/api/deep-kriging")
async def deep_kriging(request: Request):
    """DeepKriging™ RBF estimation endpoint."""
    t0 = time.time()

    if not HAS_SCIPY_RBF:
        return JSONResponse(
            {"error": "scipy.interpolate.RBFInterpolator not available"},
            status_code=503,
        )

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON body"}, status_code=400)

    # --- Validate required fields ---
    required = ["data_x", "data_y", "data_z", "data_values", "block_model"]
    missing = [f for f in required if f not in body]
    if missing:
        return JSONResponse(
            {"error": f"Missing required fields: {missing}"},
            status_code=400,
        )

    bm = body["block_model"]
    for k in ["origin_x", "origin_y", "origin_z",
              "block_size_x", "block_size_y", "block_size_z",
              "num_x", "num_y", "num_z"]:
        if k not in bm:
            return JSONResponse(
                {"error": f"Missing block_model.{k}"},
                status_code=400,
            )

    data_x = np.array(body["data_x"], dtype=float)
    data_y = np.array(body["data_y"], dtype=float)
    data_z = np.array(body["data_z"], dtype=float)
    data_values = np.array(body["data_values"], dtype=float)

    if not (len(data_x) == len(data_y) == len(data_z) == len(data_values)):
        return JSONResponse(
            {"error": "data_x/y/z/values must have equal length"},
            status_code=400,
        )
    if len(data_x) < 3:
        return JSONResponse(
            {"error": "Need at least 3 sample points"},
            status_code=400,
        )

    # --- Config ---
    config = body.get("config", {})
    kernel = config.get("kernel", "gaussian")
    epsilon = float(config.get("epsilon", 0))
    smoothing = float(config.get("smoothing", 0.01))
    max_neighbors = int(config.get("max_neighbors", 24))
    search_radius = float(config.get("search_radius", 0))
    adaptive_epsilon = bool(config.get("adaptive_epsilon", True))

    if kernel not in KERNEL_MAP:
        return JSONResponse(
            {"error": f"Unknown kernel '{kernel}'. Supported: {list(KERNEL_MAP.keys())}"},
            status_code=400,
        )

    # --- Build grid ---
    grid_xyz, grid_indices = _build_block_grid(
        bm["origin_x"], bm["origin_y"], bm["origin_z"],
        bm["block_size_x"], bm["block_size_y"], bm["block_size_z"],
        int(bm["num_x"]), int(bm["num_y"]), int(bm["num_z"]),
    )

    data_xyz = np.column_stack([data_x, data_y, data_z])

    logger.info(
        f"DeepKriging™ start: {len(data_x)} samples → "
        f"{len(grid_xyz)} blocks, kernel={kernel}"
    )

    # --- Run DeepKriging ---
    estimates, variances, meta = _deep_kriging_rbf(
        data_xyz, data_values, grid_xyz,
        kernel=kernel,
        epsilon=epsilon,
        smoothing=smoothing,
        max_neighbors=max_neighbors,
        search_radius=search_radius,
        adaptive_epsilon=adaptive_epsilon,
    )

    elapsed_ms = (time.time() - t0) * 1000

    # --- Build response (only estimated blocks) ---
    valid_mask = ~np.isnan(estimates)
    valid_idx = np.where(valid_mask)[0]

    blocks = []
    for idx in valid_idx:
        ix, iy, iz = grid_indices[idx]
        blocks.append({
            "ix": int(ix),
            "iy": int(iy),
            "iz": int(iz),
            "x": float(grid_xyz[idx, 0]),
            "y": float(grid_xyz[idx, 1]),
            "z": float(grid_xyz[idx, 2]),
            "estimate": round(float(estimates[idx]), 6),
            "variance": round(float(variances[idx]), 6),
            "std_dev": round(float(np.sqrt(max(0, variances[idx]))), 6),
        })

    response = {
        "method": "DeepKriging-RBF",
        "engine": "scipy.interpolate.RBFInterpolator",
        "execution_time_ms": round(elapsed_ms, 1),
        "config": {
            "kernel": meta["kernel"],
            "epsilon": meta["epsilon_used"],
            "smoothing": smoothing,
            "max_neighbors": max_neighbors,
            "search_radius": meta["search_radius"],
            "adaptive_epsilon": adaptive_epsilon,
        },
        "summary": meta,
        "blocks": blocks,
        "_compute_source": "microservice",
    }

    logger.info(
        f"DeepKriging™ done: {meta['estimated_blocks']}/{meta['total_blocks']} blocks "
        f"in {elapsed_ms:.0f}ms (kernel={meta['kernel']})"
    )

    return JSONResponse(response)
