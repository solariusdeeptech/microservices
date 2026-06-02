"""
POST /mps — Simulation Multi-Points (MPS)

Utilise la méthode de simulation par patrons (pattern-based) pour
reproduire la connectivité géologique à partir d'images d'entraînement.

Input JSON:
{
  "training_image": [[0,1,1,...], [1,0,1,...], ...],  // 2D grid (0=waste, 1=ore)
  "grid_size": { "nx": 100, "ny": 100, "nz": 1 },
  "template_size": { "x": 5, "y": 5 },
  "num_realizations": 10,
  "conditioning_data": [
    { "ix": 10, "iy": 20, "value": 1 },
    ...
  ],
  "seed": 42
}

Output JSON:
{
  "realizations": [
    [[0,1,...], [1,0,...], ...],  // realization 1
    ...
  ],
  "statistics": {
    "mean_proportion": 0.35,
    "variance": 0.02,
    "connectivity": 0.85
  }
}
"""

import time
from typing import Optional

import numpy as np
from fastapi import APIRouter
from pydantic import BaseModel
from loguru import logger

router = APIRouter()


class GridSize(BaseModel):
    nx: int = 100
    ny: int = 100
    nz: int = 1


class TemplateSize(BaseModel):
    x: int = 5
    y: int = 5


class ConditioningPoint(BaseModel):
    ix: int
    iy: int
    value: int


class MPSRequest(BaseModel):
    training_image: list[list[int]]
    grid_size: GridSize = GridSize()
    template_size: TemplateSize = TemplateSize()
    num_realizations: int = 10
    conditioning_data: list[ConditioningPoint] = []
    seed: int = 42


def extract_patterns(ti: np.ndarray, template_x: int, template_y: int) -> list[np.ndarray]:
    """Extrait tous les patrons de l'image d'entraînement."""
    patterns = []
    ny, nx = ti.shape
    for j in range(ny - template_y + 1):
        for i in range(nx - template_x + 1):
            pattern = ti[j:j + template_y, i:i + template_x].copy()
            patterns.append(pattern)
    return patterns


def find_best_pattern(
    patterns: list[np.ndarray],
    neighborhood: np.ndarray,
    mask: np.ndarray,
) -> np.ndarray:
    """Trouve le patron le plus compatible avec le voisinage connu."""
    best_pattern = patterns[0]
    best_score = -1

    for pattern in patterns:
        # Compare only where mask indicates known values
        score = np.sum((pattern == neighborhood) & mask)
        if score > best_score:
            best_score = score
            best_pattern = pattern

    return best_pattern


def simulate_mps(
    ti: np.ndarray,
    nx: int, ny: int,
    template_x: int, template_y: int,
    conditioning: list[ConditioningPoint],
    rng: np.random.Generator,
) -> np.ndarray:
    """Exécute une réalisation MPS (Direct Sampling simplifié)."""
    grid = np.full((ny, nx), -1, dtype=int)

    # Apply conditioning data
    for cp in conditioning:
        if 0 <= cp.iy < ny and 0 <= cp.ix < nx:
            grid[cp.iy, cp.ix] = cp.value

    # Extract patterns from training image
    patterns = extract_patterns(ti, template_x, template_y)

    if not patterns:
        return rng.integers(0, 2, size=(ny, nx))

    # Random path through grid
    unknown = [(j, i) for j in range(ny) for i in range(nx) if grid[j, i] == -1]
    rng.shuffle(unknown)

    half_tx = template_x // 2
    half_ty = template_y // 2

    for (j, i) in unknown:
        # Extract neighborhood
        j_start = max(0, j - half_ty)
        j_end = min(ny, j + half_ty + 1)
        i_start = max(0, i - half_tx)
        i_end = min(nx, i + half_tx + 1)

        neighborhood = np.full((template_y, template_x), -1, dtype=int)
        mask = np.zeros((template_y, template_x), dtype=bool)

        for jj in range(j_start, j_end):
            for ii in range(i_start, i_end):
                nj = jj - j + half_ty
                ni = ii - i + half_tx
                if 0 <= nj < template_y and 0 <= ni < template_x and grid[jj, ii] != -1:
                    neighborhood[nj, ni] = grid[jj, ii]
                    mask[nj, ni] = True

        if mask.any():
            best = find_best_pattern(patterns, neighborhood, mask)
            grid[j, i] = best[half_ty, half_tx]
        else:
            # No neighbors known: sample randomly from TI proportions
            grid[j, i] = rng.choice([0, 1], p=[
                1 - ti.mean(), ti.mean()
            ])

    return grid


@router.post("/api/mps")
async def run_mps(req: MPSRequest):
    start = time.time()

    ti = np.array(req.training_image, dtype=int)
    logger.info(f"MPS request: TI shape={ti.shape}, grid={req.grid_size.nx}x{req.grid_size.ny}, "
                f"{req.num_realizations} réalisations")

    rng = np.random.default_rng(req.seed)
    realizations = []

    for r in range(req.num_realizations):
        realization = simulate_mps(
            ti, req.grid_size.nx, req.grid_size.ny,
            req.template_size.x, req.template_size.y,
            req.conditioning_data, rng,
        )
        realizations.append(realization.tolist())

    # Compute statistics
    all_reals = np.array(realizations, dtype=float)
    mean_proportion = float(all_reals.mean())
    variance = float(all_reals.var())

    elapsed = round(time.time() - start, 3)

    return {
        "realizations": realizations,
        "statistics": {
            "mean_proportion": mean_proportion,
            "variance": variance,
            "num_realizations": req.num_realizations,
        },
        "metadata": {
            "grid_size": f"{req.grid_size.nx}x{req.grid_size.ny}",
            "template_size": f"{req.template_size.x}x{req.template_size.y}",
            "conditioning_points": len(req.conditioning_data),
            "processing_time_s": elapsed,
            "engine": "Solarius MPS/Direct Sampling",
        }
    }
