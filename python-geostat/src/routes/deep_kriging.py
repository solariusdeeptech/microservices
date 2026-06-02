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
    """DeepKriging™ endpoint — supports RBF (legacy) and MLP (new) modes."""
    t0 = time.time()

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON body"}, status_code=400)

    # Detect contract: new (has 'points' + 'grid') vs legacy (has 'data_x' + 'block_model')
    if "points" in body and "grid" in body:
        return await _deep_kriging_mlp(body, t0)

    if not HAS_SCIPY_RBF:
        return JSONResponse(
            {"error": "scipy.interpolate.RBFInterpolator not available"},
            status_code=503,
        )

    # --- Legacy RBF mode ---
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


# ========== New MLP-based DeepKriging ==========

async def _deep_kriging_mlp(body: dict, t0: float):
    """
    MLP-based Deep Kriging: learns spatial basis functions via neural network.
    Uses scikit-learn MLPRegressor (no PyTorch dependency needed).

    Input:
      points: [{x, y, z, value}]
      grid: {origin: {x,y,z}, size: {nx,ny,nz}, spacing: {dx,dy,dz}}
      epochs, hidden_layers, activation, learning_rate, batch_size
    """
    from sklearn.neural_network import MLPRegressor
    from sklearn.preprocessing import StandardScaler
    from sklearn.model_selection import cross_val_score
    from scipy.spatial import cKDTree

    points = body.get("points", [])
    grid = body.get("grid", {})

    if len(points) < 5:
        return JSONResponse({"error": "Need at least 5 points for MLP training"}, status_code=400)

    # Parse config
    epochs = body.get("epochs", 500)
    hidden_layers = body.get("hidden_layers", [128, 64, 32])
    activation = body.get("activation", "relu")
    lr = body.get("learning_rate", 0.001)
    batch_size = body.get("batch_size", min(64, len(points)))

    # Extract coordinates and values
    coords = np.array([[p["x"], p["y"], p["z"]] for p in points], dtype=float)
    values = np.array([p["value"] for p in points], dtype=float)

    # Build spatial features: (x, y, z, dist_to_k_nearest, normalized_coords)
    n_neighbors = min(8, len(points) - 1)
    tree = cKDTree(coords)
    dists, _ = tree.query(coords, k=n_neighbors + 1)
    nn_dists = dists[:, 1:]  # exclude self

    # Feature matrix: raw coords + distance features
    features = np.column_stack([
        coords,                          # x, y, z
        nn_dists.mean(axis=1),          # mean NN distance
        nn_dists.min(axis=1),           # min NN distance
        nn_dists.max(axis=1),           # max NN distance
    ])

    # Normalize features
    scaler_X = StandardScaler()
    features_scaled = scaler_X.fit_transform(features)

    scaler_y = StandardScaler()
    values_scaled = scaler_y.fit_transform(values.reshape(-1, 1)).ravel()

    # Train MLP
    mlp = MLPRegressor(
        hidden_layer_sizes=tuple(hidden_layers),
        activation=activation,
        solver='adam',
        learning_rate_init=lr,
        max_iter=epochs,
        batch_size=min(batch_size, len(points)),
        early_stopping=True,
        validation_fraction=0.15,
        n_iter_no_change=50,
        random_state=42,
        verbose=False,
    )

    mlp.fit(features_scaled, values_scaled)

    # Training metrics
    train_pred_scaled = mlp.predict(features_scaled)
    train_pred = scaler_y.inverse_transform(train_pred_scaled.reshape(-1, 1)).ravel()

    ss_res = np.sum((values - train_pred) ** 2)
    ss_tot = np.sum((values - np.mean(values)) ** 2)
    r2 = float(1 - ss_res / ss_tot) if ss_tot > 0 else 0.0
    rmse = float(np.sqrt(np.mean((values - train_pred) ** 2)))
    final_loss = float(mlp.loss_) if hasattr(mlp, 'loss_') else 0.0

    # Build prediction grid
    origin = grid.get("origin", {"x": 0, "y": 0, "z": 0})
    size = grid.get("size", {"nx": 10, "ny": 10, "nz": 5})
    spacing = grid.get("spacing", {"dx": 10, "dy": 10, "dz": 5})

    ox, oy, oz = origin["x"], origin["y"], origin["z"]
    nx, ny, nz = size["nx"], size["ny"], size["nz"]
    dx, dy, dz = spacing["dx"], spacing["dy"], spacing["dz"]

    total_blocks = nx * ny * nz
    if total_blocks > 2_000_000:
        return JSONResponse(
            {"error": f"Grid too large: {total_blocks} blocks (max 2M)"},
            status_code=400,
        )

    # Generate grid centers
    ix = np.arange(nx)
    iy = np.arange(ny)
    iz = np.arange(nz)
    gx, gy, gz = np.meshgrid(ix, iy, iz, indexing='ij')
    gx, gy, gz = gx.ravel(), gy.ravel(), gz.ravel()

    grid_coords = np.column_stack([
        ox + (gx + 0.5) * dx,
        oy + (gy + 0.5) * dy,
        oz + (gz + 0.5) * dz,
    ])

    # Build features for grid points
    BATCH = 10000
    grid_features_list = []
    for start in range(0, len(grid_coords), BATCH):
        end = min(start + BATCH, len(grid_coords))
        batch_coords = grid_coords[start:end]
        batch_dists, _ = tree.query(batch_coords, k=n_neighbors)

        batch_features = np.column_stack([
            batch_coords,
            batch_dists.mean(axis=1),
            batch_dists.min(axis=1),
            batch_dists.max(axis=1),
        ])
        grid_features_list.append(batch_features)

    grid_features = np.vstack(grid_features_list)
    grid_features_scaled = scaler_X.transform(grid_features)

    # Predict
    grid_pred_scaled = mlp.predict(grid_features_scaled)
    estimated_values = scaler_y.inverse_transform(grid_pred_scaled.reshape(-1, 1)).ravel()

    # Clamp negatives
    estimated_values = np.clip(estimated_values, 0, None)

    elapsed_ms = int((time.time() - t0) * 1000)

    return {
        "estimated_values": [round(float(v), 6) for v in estimated_values],
        "grid_size": [nx, ny, nz],
        "grid_origin": [ox, oy, oz],
        "grid_spacing": [dx, dy, dz],
        "training_metrics": {
            "final_loss": round(final_loss, 6),
            "epochs_trained": mlp.n_iter_,
            "r2_score": round(r2, 4),
            "rmse": round(rmse, 6),
            "n_training_points": len(points),
            "hidden_layers": hidden_layers,
            "activation": activation,
        },
        "method": "DeepKriging-MLP",
        "engine": "scikit-learn.MLPRegressor",
        "compute_time_ms": elapsed_ms,
        "_compute_source": "microservice",
    }
