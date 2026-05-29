"""
POST /api/blockmodel — Block model estimation with JORC classification.
API contract aligned with TypeScript BlockModelEstimationRequest/Response types.
"""
import time
import logging
import numpy as np
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/api/blockmodel")
async def block_model(request: Request):
    t0 = time.time()
    body = await request.json()

    # TS BlockModelEstimationRequest fields
    bm = body["block_model"]
    data_x = np.array(body["data_x"], dtype=float)
    data_y = np.array(body["data_y"], dtype=float)
    data_z = np.array(body["data_z"], dtype=float)
    data_values = np.array(body["data_values"], dtype=float)
    variogram = body.get("variogram", {})
    density = body.get("density", 2.7)  # t/m³
    classify = body.get("classify", True)

    origin_x = bm["origin_x"]
    origin_y = bm["origin_y"]
    origin_z = bm["origin_z"]
    bs_x = bm["block_size_x"]
    bs_y = bm["block_size_y"]
    bs_z = bm["block_size_z"]
    n_x = bm["n_blocks_x"]
    n_y = bm["n_blocks_y"]
    n_z = bm["n_blocks_z"]
    model_name = bm.get("name", "block_model")

    n_blocks = n_x * n_y * n_z
    block_vol = bs_x * bs_y * bs_z
    block_tonnage = block_vol * density
    n_data = len(data_x)
    logger.info(f"Block model: {n_blocks} blocks, {n_data} data points")

    # Generate block centroids
    cx = np.array([origin_x + (i + 0.5) * bs_x for i in range(n_x)])
    cy = np.array([origin_y + (j + 0.5) * bs_y for j in range(n_y)])
    cz = np.array([origin_z + (k + 0.5) * bs_z for k in range(n_z)])
    gx, gy, gz = np.meshgrid(cx, cy, cz, indexing='ij')
    grid_x = gx.flatten()
    grid_y = gy.flatten()
    grid_z = gz.flatten()

    # IDW estimation for each block (fast, reliable)
    max_neighbors = 12
    estimates = np.zeros(n_blocks)
    variances = np.zeros(n_blocks)

    for i in range(n_blocks):
        dists = np.sqrt(
            (data_x - grid_x[i])**2 +
            (data_y - grid_y[i])**2 +
            (data_z - grid_z[i])**2
        )
        idx = np.argsort(dists)[:max_neighbors]
        w = 1.0 / np.maximum(dists[idx], 1e-10) ** 2
        estimates[i] = np.sum(w * data_values[idx]) / np.sum(w)
        variances[i] = np.var(data_values[idx])

    # JORC classification based on variance and data density
    if classify:
        search_radii = np.array([bs_x * 2, bs_x * 4, bs_x * 8])  # measured, indicated, inferred
        categories = np.full(n_blocks, 3, dtype=int)  # 3=inferred by default

        for i in range(n_blocks):
            dists = np.sqrt(
                (data_x - grid_x[i])**2 +
                (data_y - grid_y[i])**2 +
                (data_z - grid_z[i])**2
            )
            n_near = np.sum(dists <= search_radii[0])
            n_mid = np.sum(dists <= search_radii[1])

            if n_near >= 4:
                categories[i] = 1  # measured
            elif n_mid >= 3:
                categories[i] = 2  # indicated

        # Resource summary
        def make_category(mask):
            nb = int(np.sum(mask))
            return {
                "n_blocks": nb,
                "tonnage_t": float(nb * block_tonnage),
                "avg_grade": float(np.mean(estimates[mask])) if nb > 0 else 0.0,
            }

        measured = make_category(categories == 1)
        indicated = make_category(categories == 2)
        inferred = make_category(categories == 3)
        total = make_category(np.ones(n_blocks, dtype=bool))
    else:
        measured = {"n_blocks": 0, "tonnage_t": 0.0, "avg_grade": 0.0}
        indicated = {"n_blocks": 0, "tonnage_t": 0.0, "avg_grade": 0.0}
        inferred = {"n_blocks": n_blocks, "tonnage_t": float(n_blocks * block_tonnage), "avg_grade": float(np.mean(estimates))}
        total = inferred.copy()

    elapsed = round(time.time() - t0, 3)

    # Response matches TS BlockModelEstimationResponse
    return JSONResponse({
        "status": "success",
        "block_model": {
            "name": model_name,
            "n_blocks": n_blocks,
            "block_volume_m3": float(block_vol),
            "block_tonnage_t": float(block_tonnage),
        },
        "resource_summary": {
            "measured": measured,
            "indicated": indicated,
            "inferred": inferred,
            "total": total,
        },
    }, headers={"X-Process-Time": str(elapsed)})
