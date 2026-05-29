"""
POST /api/pit-optimization — Lerchs-Grossmann pit optimization.
API contract aligned with TypeScript PitOptimizationRequest/Response types.
"""
import time
import logging
import numpy as np
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/api/pit-optimization")
async def pit_optimize(request: Request):
    t0 = time.time()
    body = await request.json()

    # TS PitOptimizationRequest fields
    econ = body["economic_params"]
    geotech = body.get("geotechnical_params", {})
    bm_data = body.get("block_model", {})

    commodity_price = econ["commodity_price"]
    mining_cost = econ["mining_cost"]
    processing_cost = econ["processing_cost"]
    recovery = econ.get("recovery", 0.92)
    dilution = econ.get("dilution", 0.05)
    cutoff_grade = econ.get("cutoff_grade", 0.5)

    slope_angle = geotech.get("slope_angles", {}).get("overall", 45)
    bench_height = geotech.get("bench_height", 10)

    grid = bm_data.get("grid", {})
    grades = np.array(bm_data.get("grades", []), dtype=float)
    densities = np.array(bm_data.get("densities", []), dtype=float)

    nx = grid.get("nx", 10)
    ny = grid.get("ny", 10)
    nz = grid.get("nz", 10)
    bs_x = grid.get("block_size_x", 10)
    bs_y = grid.get("block_size_y", 10)
    bs_z = grid.get("block_size_z", 10)

    n_blocks = nx * ny * nz
    block_vol = bs_x * bs_y * bs_z

    # Default densities if not provided
    if len(densities) == 0:
        densities = np.full(n_blocks, 2.7)
    if len(grades) == 0:
        grades = np.random.default_rng(42).uniform(0, 5, n_blocks)

    logger.info(f"Pit optimization: {n_blocks} blocks ({nx}x{ny}x{nz})")

    # Calculate block economic values (NSR - costs)
    block_tonnages = block_vol * densities
    revenue_per_block = block_tonnages * grades * (1 - dilution) * recovery * commodity_price / 31.1035
    cost_per_block = block_tonnages * (mining_cost + processing_cost)
    block_values = revenue_per_block - cost_per_block

    # Simplified Lerchs-Grossmann: greedy column-based approach
    # Reshape to 3D grid (nx, ny, nz) - z=0 is top
    values_3d = block_values.reshape(nx, ny, nz)
    grades_3d = grades.reshape(nx, ny, nz)
    tonnages_3d = block_tonnages.reshape(nx, ny, nz)

    in_pit = np.zeros((nx, ny, nz), dtype=bool)

    # For each column, find optimal depth
    slope_tan = np.tan(np.radians(slope_angle))
    for ix in range(nx):
        for iy in range(ny):
            cumulative = 0.0
            best_depth = -1
            best_value = 0.0
            running = 0.0
            for iz in range(nz):
                running += values_3d[ix, iy, iz]
                if running > best_value:
                    best_value = running
                    best_depth = iz

            if best_depth >= 0 and best_value > 0:
                for iz in range(best_depth + 1):
                    in_pit[ix, iy, iz] = True

    # Apply slope constraints (expand pit laterally for deeper blocks)
    for iz in range(1, nz):
        expansion = int(np.ceil(iz * bs_z / (slope_tan * bs_x))) if slope_tan > 0 else 0
        for ix in range(nx):
            for iy in range(ny):
                if in_pit[ix, iy, iz]:
                    for dx in range(-expansion, expansion + 1):
                        for dy in range(-expansion, expansion + 1):
                            nix, niy = ix + dx, iy + dy
                            if 0 <= nix < nx and 0 <= niy < ny:
                                for dz in range(iz):
                                    in_pit[nix, niy, dz] = True

    # Statistics
    pit_mask = in_pit.flatten()
    pit_grades = grades[pit_mask]
    pit_tonnages = block_tonnages[pit_mask]
    ore_mask = pit_grades >= cutoff_grade

    ore_tonnage = float(np.sum(pit_tonnages[ore_mask]))
    waste_tonnage = float(np.sum(pit_tonnages[~ore_mask]))
    blocks_in_pit = int(np.sum(pit_mask))
    avg_grade = float(np.mean(pit_grades[ore_mask])) if np.any(ore_mask) else 0.0
    contained_metal = float(np.sum(pit_tonnages[ore_mask] * pit_grades[ore_mask] * recovery / 31.1035))
    total_nsr = float(np.sum(block_values[pit_mask]))
    pit_depth = float(np.max(np.where(in_pit.any(axis=(0, 1)))[0] + 1) * bs_z) if blocks_in_pit > 0 else 0.0

    # Pit block IDs
    pit_block_ids = [f"blk_{i}" for i in range(n_blocks) if pit_mask[i]]

    elapsed = round(time.time() - t0, 3)

    # Response matches TS PitOptimizationResponse
    return JSONResponse({
        "status": "success",
        "optimization": {
            "algorithm": "lerchs-grossmann-simplified",
            "blocks_evaluated": n_blocks,
            "blocks_in_pit": blocks_in_pit,
        },
        "statistics": {
            "blocks_in_pit": blocks_in_pit,
            "ore_tonnage_t": ore_tonnage,
            "waste_tonnage_t": waste_tonnage,
            "strip_ratio": float(waste_tonnage / max(ore_tonnage, 1)),
            "average_grade": avg_grade,
            "contained_metal_oz": contained_metal,
            "total_nsr": total_nsr,
            "pit_depth_m": pit_depth,
        },
        "pit_block_ids": pit_block_ids,
        "performance": {
            "total_time_s": elapsed,
        },
    }, headers={"X-Process-Time": str(elapsed)})
