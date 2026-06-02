"""
POST /api/faults — Structural fault operations.

Supported actions (via 'action' field):
  - compartmentalize: Divide model into fault-bounded compartments
  - clip: Clip a mesh by fault planes (hanging/footwall selection)
  - unfault: Remove fault displacement from 3D points
  - refault: Re-apply fault displacement to 3D points
  - validate: Validate fault displacement consistency

Also supports sub-routes:
  POST /api/faults/compartmentalize
  POST /api/faults/clip
  POST /api/faults/unfault
"""
import time
import math
import logging
import numpy as np
from fastapi import APIRouter
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)
router = APIRouter()


def _fault_plane_normal(strike_deg: float, dip_deg: float) -> np.ndarray:
    """Compute unit normal vector of a fault plane from strike and dip."""
    strike_rad = math.radians(strike_deg)
    dip_rad = math.radians(dip_deg)
    # Normal = perpendicular to strike, tilted by dip
    nx = math.sin(dip_rad) * math.sin(strike_rad + math.pi / 2)
    ny = math.sin(dip_rad) * math.cos(strike_rad + math.pi / 2)
    nz = math.cos(dip_rad)
    n = np.array([nx, ny, nz])
    return n / np.linalg.norm(n)


def _signed_distance_to_plane(point: np.ndarray, plane_point: np.ndarray, normal: np.ndarray) -> float:
    """Signed distance from a point to a plane."""
    return float(np.dot(point - plane_point, normal))


def _compartmentalize(body: dict) -> dict:
    """Divide a model volume into compartments using fault planes."""
    faults = body.get("faults", [])
    bounds = body.get("model_bounds", {})
    resolution = body.get("grid_resolution", 10)

    if not faults:
        return {"error": "No faults provided"}

    bmin = bounds.get("min", {"x": 0, "y": 0, "z": 0})
    bmax = bounds.get("max", {"x": 100, "y": 100, "z": 100})

    # Build fault planes
    fault_planes = []
    for f in faults:
        pos = f.get("position", {"x": 0, "y": 0, "z": 0})
        normal = _fault_plane_normal(f.get("strike", 0), f.get("dip", 90))
        fault_planes.append({
            "id": f.get("id", f"F{len(fault_planes)+1}"),
            "name": f.get("name", ""),
            "point": np.array([pos["x"], pos["y"], pos["z"]]),
            "normal": normal,
            "strike": f.get("strike", 0),
            "dip": f.get("dip", 90),
        })

    # Create regular grid
    xs = np.arange(bmin["x"], bmax["x"], resolution)
    ys = np.arange(bmin["y"], bmax["y"], resolution)
    zs = np.arange(bmin["z"], bmax["z"], resolution)
    grid = np.array(np.meshgrid(xs, ys, zs, indexing='ij')).reshape(3, -1).T
    n_cells = len(grid)

    # Assign compartment ID: binary encoding based on which side of each fault
    compartment_ids = np.zeros(n_cells, dtype=int)
    for i, fp in enumerate(fault_planes):
        distances = (grid - fp["point"]) @ fp["normal"]
        compartment_ids += (distances > 0).astype(int) * (2 ** i)

    # Group cells by compartment
    unique_ids = np.unique(compartment_ids)
    compartments = []
    for cid in unique_ids:
        mask = compartment_ids == cid
        cells = grid[mask]
        centroid = cells.mean(axis=0)
        volume = float(len(cells)) * resolution**3

        # Determine which side of each fault
        sides = {}
        for j, fp in enumerate(fault_planes):
            if cid & (2 ** j):
                sides[fp["id"]] = "hanging"
            else:
                sides[fp["id"]] = "footwall"

        compartments.append({
            "compartment_id": int(cid),
            "n_cells": int(mask.sum()),
            "volume": volume,
            "centroid": {"x": float(centroid[0]), "y": float(centroid[1]), "z": float(centroid[2])},
            "fault_sides": sides,
        })

    return {
        "n_compartments": len(compartments),
        "n_faults": len(fault_planes),
        "grid_resolution": resolution,
        "total_cells": n_cells,
        "compartments": compartments,
        "faults": [{"id": fp["id"], "name": fp["name"], "strike": fp["strike"], "dip": fp["dip"],
                     "normal": fp["normal"].tolist()} for fp in fault_planes],
    }


def _clip(body: dict) -> dict:
    """Clip a mesh or voxel model by fault planes."""
    vertices = np.array(body.get("vertices", []), dtype=float)
    faces = np.array(body.get("faces", []), dtype=int) if body.get("faces") else None
    faults = body.get("faults", [])
    compartment_sides = body.get("compartment_sides", {})
    mode = body.get("mode", "mesh")

    if len(vertices) == 0:
        return {"error": "No vertices provided"}
    if not faults:
        return {"error": "No faults provided"}

    # Filter vertices by fault plane sides
    keep_mask = np.ones(len(vertices), dtype=bool)

    for f in faults:
        fid = f.get("id", "")
        desired_side = compartment_sides.get(fid, "hanging")
        pos = f.get("position", {"x": 0, "y": 0, "z": 0})
        normal = _fault_plane_normal(f.get("strike", 0), f.get("dip", 90))
        plane_point = np.array([pos["x"], pos["y"], pos["z"]])

        distances = (vertices - plane_point) @ normal

        if desired_side == "hanging":
            keep_mask &= (distances >= 0)
        else:  # footwall
            keep_mask &= (distances < 0)

    kept_indices = np.where(keep_mask)[0]
    kept_vertices = vertices[keep_mask]

    result = {
        "n_original_vertices": len(vertices),
        "n_kept_vertices": len(kept_vertices),
        "vertices": kept_vertices.tolist(),
        "mode": mode,
    }

    # If mesh mode, rebuild faces that still have all 3 vertices
    if faces is not None and len(faces) > 0 and mode == "mesh":
        old_to_new = {old: new for new, old in enumerate(kept_indices)}
        new_faces = []
        for face in faces:
            if all(int(v) in old_to_new for v in face):
                new_faces.append([old_to_new[int(v)] for v in face])
        result["faces"] = new_faces
        result["n_original_faces"] = len(faces)
        result["n_kept_faces"] = len(new_faces)

    return result


def _unfault(body: dict) -> dict:
    """Remove or re-apply fault displacement to 3D points."""
    action = body.get("action", "unfault")
    points = body.get("points", [])
    faults = body.get("faults", [])
    sequence = body.get("sequence", [f.get("id") for f in faults])

    if not points:
        return {"error": "No points provided"}
    if not faults:
        return {"error": "No faults provided"}

    pts = np.array([[p["x"], p["y"], p["z"]] for p in points], dtype=float)

    # Build fault lookup
    fault_map = {}
    for f in faults:
        pos = f.get("position", {"x": 0, "y": 0, "z": 0})
        normal = _fault_plane_normal(f.get("strike", 0), f.get("dip", 90))
        displacement = f.get("displacement", 0)
        fault_map[f["id"]] = {
            "point": np.array([pos["x"], pos["y"], pos["z"]]),
            "normal": normal,
            "displacement": displacement,
        }

    # Process faults in sequence order
    moved_counts = {}
    if action in ("unfault", "validate"):
        # Reverse order for unfaulting
        process_order = list(reversed(sequence))
    else:  # refault
        process_order = list(sequence)

    for fid in process_order:
        if fid not in fault_map:
            continue
        fp = fault_map[fid]
        distances = (pts - fp["point"]) @ fp["normal"]

        if action == "unfault":
            # Points on hanging wall: move them by -displacement along normal
            hanging = distances > 0
            pts[hanging] -= fp["normal"] * fp["displacement"]
            moved_counts[fid] = int(hanging.sum())
        elif action == "refault":
            hanging = distances > 0
            pts[hanging] += fp["normal"] * fp["displacement"]
            moved_counts[fid] = int(hanging.sum())
        elif action == "validate":
            # Check displacement consistency
            hanging = distances > 0
            moved_counts[fid] = {
                "hanging_count": int(hanging.sum()),
                "footwall_count": int((~hanging).sum()),
                "displacement": fp["displacement"],
                "mean_distance": float(np.abs(distances).mean()),
            }

    result_points = [{"x": float(p[0]), "y": float(p[1]), "z": float(p[2])} for p in pts]

    return {
        "action": action,
        "n_points": len(result_points),
        "points": result_points,
        "sequence": process_order,
        "fault_stats": moved_counts,
    }


@router.post("/api/faults")
async def faults_main(request):
    """Main faults endpoint — dispatches based on 'action' field."""
    t0 = time.time()
    body = await request.json()
    action = body.get("action", "").lower()

    if action == "compartmentalize":
        result = _compartmentalize(body)
    elif action == "clip":
        result = _clip(body)
    elif action in ("unfault", "refault", "validate"):
        result = _unfault(body)
    else:
        return JSONResponse(status_code=400, content={
            "error": f"Unknown action '{action}'",
            "valid_actions": ["compartmentalize", "clip", "unfault", "refault", "validate"]
        })

    if isinstance(result, dict) and "error" in result and "n_" not in str(result.keys()):
        return JSONResponse(status_code=400, content=result)

    result["processing_time_ms"] = int((time.time() - t0) * 1000)
    return result


@router.post("/api/faults/compartmentalize")
async def faults_compartmentalize(request):
    t0 = time.time()
    body = await request.json()
    body["action"] = "compartmentalize"
    result = _compartmentalize(body)
    result["processing_time_ms"] = int((time.time() - t0) * 1000)
    return result


@router.post("/api/faults/clip")
async def faults_clip(request):
    t0 = time.time()
    body = await request.json()
    body["action"] = "clip"
    result = _clip(body)
    result["processing_time_ms"] = int((time.time() - t0) * 1000)
    return result


@router.post("/api/faults/unfault")
async def faults_unfault(request):
    t0 = time.time()
    body = await request.json()
    if "action" not in body:
        body["action"] = "unfault"
    result = _unfault(body)
    result["processing_time_ms"] = int((time.time() - t0) * 1000)
    return result
