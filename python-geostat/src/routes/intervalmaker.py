"""
POST /api/intervalmaker — Automatic mineralized interval detection and 3D envelope generation.

Supported actions (via 'action' field):
  - detect: Detect mineralized intervals from drillhole samples
  - envelope: Generate 3D alpha-shape envelopes from interval points

Also supports sub-routes:
  POST /api/intervalmaker/detect
  POST /api/intervalmaker/envelope
"""
import time
import math
import logging
import numpy as np
from scipy.spatial import Delaunay, ConvexHull
from fastapi import APIRouter
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)
router = APIRouter()


def _detect_intervals(body: dict) -> dict:
    """Detect mineralized intervals from drillhole assay data."""
    samples = body.get("samples", [])
    trajectories = body.get("trajectories", {})
    params = body.get("params", {})
    correlation_params = body.get("correlation_params", {})

    cutoff = params.get("cutoff", 0.5)
    min_thickness = params.get("min_thickness", 1.0)
    max_dilution = params.get("max_dilution_meters", 2.0)
    gap_tolerance = params.get("gap_tolerance", 0.5)
    interval_name = params.get("interval_name", "Zone")

    max_correlation_dist = correlation_params.get("max_distance", 100)
    max_correlation_angle = correlation_params.get("max_angle", 30)
    min_intervals_per_vein = correlation_params.get("min_intervals_per_vein", 3)

    if not samples:
        return {"error": "No samples provided"}

    # Group samples by hole_id
    holes = {}
    for s in samples:
        hid = s.get("hole_id", "")
        if hid not in holes:
            holes[hid] = []
        holes[hid].append(s)

    # Sort each hole by from_depth
    for hid in holes:
        holes[hid].sort(key=lambda x: x.get("from", 0))

    all_intervals = []
    interval_id = 0

    for hid, hole_samples in holes.items():
        # Phase 1: Identify above-cutoff runs
        in_interval = False
        current_interval = None
        gap_accumulated = 0.0

        for sample in hole_samples:
            grade = sample.get("grade", 0)
            from_d = sample.get("from", 0)
            to_d = sample.get("to", from_d + 1)
            thickness = to_d - from_d

            if grade >= cutoff:
                if not in_interval:
                    # Start new interval
                    current_interval = {
                        "from": from_d,
                        "to": to_d,
                        "grades": [grade],
                        "thicknesses": [thickness],
                        "n_samples": 1,
                    }
                    in_interval = True
                    gap_accumulated = 0.0
                else:
                    # Extend current interval
                    current_interval["to"] = to_d
                    current_interval["grades"].append(grade)
                    current_interval["thicknesses"].append(thickness)
                    current_interval["n_samples"] += 1
                    gap_accumulated = 0.0
            else:
                if in_interval:
                    gap_accumulated += thickness
                    if gap_accumulated <= gap_tolerance:
                        # Internal dilution — include below-cutoff sample
                        current_interval["to"] = to_d
                        current_interval["grades"].append(grade)
                        current_interval["thicknesses"].append(thickness)
                        current_interval["n_samples"] += 1
                    else:
                        # Close interval
                        total_thickness = current_interval["to"] - current_interval["from"]
                        if total_thickness >= min_thickness:
                            grades = np.array(current_interval["grades"])
                            thicknesses = np.array(current_interval["thicknesses"])
                            weighted_grade = float(np.average(grades, weights=thicknesses))

                            # Get 3D midpoint from trajectory
                            mid_depth = (current_interval["from"] + current_interval["to"]) / 2
                            midpoint = _interpolate_trajectory(trajectories.get(hid, []), mid_depth)

                            interval_id += 1
                            all_intervals.append({
                                "id": f"{interval_name}_{interval_id:04d}",
                                "hole_id": hid,
                                "from": current_interval["from"],
                                "to": current_interval["to"],
                                "thickness": total_thickness,
                                "grade": round(weighted_grade, 4),
                                "max_grade": round(float(grades.max()), 4),
                                "n_samples": current_interval["n_samples"],
                                "dilution_ratio": round(float((grades < cutoff).sum() / len(grades)), 3),
                                "midpoint": midpoint,
                            })
                        in_interval = False
                        current_interval = None
                        gap_accumulated = 0.0

        # Close last interval if still open
        if in_interval and current_interval:
            total_thickness = current_interval["to"] - current_interval["from"]
            if total_thickness >= min_thickness:
                grades = np.array(current_interval["grades"])
                thicknesses = np.array(current_interval["thicknesses"])
                weighted_grade = float(np.average(grades, weights=thicknesses))
                mid_depth = (current_interval["from"] + current_interval["to"]) / 2
                midpoint = _interpolate_trajectory(trajectories.get(hid, []), mid_depth)

                interval_id += 1
                all_intervals.append({
                    "id": f"{interval_name}_{interval_id:04d}",
                    "hole_id": hid,
                    "from": current_interval["from"],
                    "to": current_interval["to"],
                    "thickness": total_thickness,
                    "grade": round(weighted_grade, 4),
                    "max_grade": round(float(grades.max()), 4),
                    "n_samples": current_interval["n_samples"],
                    "dilution_ratio": round(float((grades < cutoff).sum() / len(grades)), 3),
                    "midpoint": midpoint,
                })

    # Phase 2: Correlate intervals into veins
    veins = _correlate_intervals(all_intervals, max_correlation_dist, max_correlation_angle, min_intervals_per_vein)

    # Statistics
    stats = {}
    if all_intervals:
        thicknesses = [iv["thickness"] for iv in all_intervals]
        grades = [iv["grade"] for iv in all_intervals]
        stats = {
            "total_intervals": len(all_intervals),
            "total_veins": len(veins),
            "holes_with_intervals": len(set(iv["hole_id"] for iv in all_intervals)),
            "holes_without_intervals": len(holes) - len(set(iv["hole_id"] for iv in all_intervals)),
            "thickness_stats": {
                "min": round(min(thicknesses), 2),
                "max": round(max(thicknesses), 2),
                "mean": round(float(np.mean(thicknesses)), 2),
                "median": round(float(np.median(thicknesses)), 2),
            },
            "grade_stats": {
                "min": round(min(grades), 4),
                "max": round(max(grades), 4),
                "mean": round(float(np.mean(grades)), 4),
                "median": round(float(np.median(grades)), 4),
            },
        }

    return {
        "intervals": all_intervals,
        "veins": veins,
        "params": {
            "cutoff": cutoff,
            "min_thickness": min_thickness,
            "max_dilution_meters": max_dilution,
            "gap_tolerance": gap_tolerance,
        },
        "statistics": stats,
    }


def _interpolate_trajectory(trajectory: list, depth: float) -> dict:
    """Interpolate 3D position along a drillhole trajectory at a given depth."""
    if not trajectory:
        return {"x": 0, "y": 0, "z": 0}

    # Sort by depth
    traj = sorted(trajectory, key=lambda t: t.get("depth", 0))

    # Find bracketing points
    for i in range(len(traj) - 1):
        d0 = traj[i].get("depth", 0)
        d1 = traj[i + 1].get("depth", 0)
        if d0 <= depth <= d1:
            if d1 == d0:
                t = 0
            else:
                t = (depth - d0) / (d1 - d0)
            return {
                "x": round(traj[i]["x"] + t * (traj[i + 1]["x"] - traj[i]["x"]), 2),
                "y": round(traj[i]["y"] + t * (traj[i + 1]["y"] - traj[i]["y"]), 2),
                "z": round(traj[i]["z"] + t * (traj[i + 1]["z"] - traj[i]["z"]), 2),
            }

    # Extrapolate from last point
    last = traj[-1]
    return {"x": round(last["x"], 2), "y": round(last["y"], 2), "z": round(last["z"], 2)}


def _correlate_intervals(intervals: list, max_dist: float, max_angle: float, min_per_vein: int) -> list:
    """Group intervals into correlated veins based on spatial proximity."""
    if not intervals:
        return []

    # Get midpoints
    midpoints = []
    for iv in intervals:
        mp = iv.get("midpoint", {"x": 0, "y": 0, "z": 0})
        midpoints.append([mp["x"], mp["y"], mp["z"]])
    midpoints = np.array(midpoints)

    # Simple distance-based clustering (greedy)
    assigned = [False] * len(intervals)
    veins = []
    vein_id = 0

    for i in range(len(intervals)):
        if assigned[i]:
            continue

        vein_intervals = [i]
        assigned[i] = True

        for j in range(i + 1, len(intervals)):
            if assigned[j]:
                continue
            # Check if same hole — skip
            if intervals[i]["hole_id"] == intervals[j]["hole_id"]:
                continue
            dist = float(np.linalg.norm(midpoints[i] - midpoints[j]))
            if dist <= max_dist:
                vein_intervals.append(j)
                assigned[j] = True

        if len(vein_intervals) >= min_per_vein:
            vein_id += 1
            vein_pts = midpoints[vein_intervals]
            veins.append({
                "vein_id": f"V{vein_id:03d}",
                "n_intervals": len(vein_intervals),
                "interval_ids": [intervals[k]["id"] for k in vein_intervals],
                "holes": list(set(intervals[k]["hole_id"] for k in vein_intervals)),
                "centroid": {
                    "x": round(float(vein_pts[:, 0].mean()), 2),
                    "y": round(float(vein_pts[:, 1].mean()), 2),
                    "z": round(float(vein_pts[:, 2].mean()), 2),
                },
                "mean_grade": round(float(np.mean([intervals[k]["grade"] for k in vein_intervals])), 4),
                "mean_thickness": round(float(np.mean([intervals[k]["thickness"] for k in vein_intervals])), 2),
            })

    return veins


def _envelope(body: dict) -> dict:
    """Generate 3D alpha-shape envelope from interval points."""
    points = body.get("points", [])
    alpha_param = body.get("alpha_param", 50)
    extrapolation_dist = body.get("extrapolation_dist", 20)
    max_edge_length = body.get("max_edge_length", 100)
    veins = body.get("veins", [])
    anisotropy_ratio = body.get("anisotropy_ratio", 1.0)

    # Collect all points (from direct points or from veins)
    all_pts = []
    if points:
        all_pts = [[p["x"], p["y"], p["z"]] for p in points]
    elif veins:
        for vein in veins:
            for interval in vein.get("intervals", []):
                for p in interval.get("points", []):
                    if isinstance(p, dict):
                        all_pts.append([p["x"], p["y"], p["z"]])
                    elif isinstance(p, (list, tuple)):
                        all_pts.append(list(p))

    if len(all_pts) < 4:
        return {"error": "Need at least 4 points to generate a 3D envelope"}

    pts = np.array(all_pts, dtype=float)

    # Apply anisotropy scaling if needed
    if anisotropy_ratio != 1.0:
        pts_scaled = pts.copy()
        pts_scaled[:, 2] *= anisotropy_ratio
    else:
        pts_scaled = pts

    try:
        # Compute Delaunay triangulation
        tri = Delaunay(pts_scaled)

        # Alpha-shape filtering: remove tetrahedra with circumradius > alpha
        alpha = alpha_param
        kept_faces = set()

        for simplex in tri.simplices:
            # Compute circumradius of tetrahedron
            tet_pts = pts_scaled[simplex]
            # Approximate: use max edge length as proxy
            edges = []
            for i in range(4):
                for j in range(i + 1, 4):
                    edges.append(np.linalg.norm(tet_pts[i] - tet_pts[j]))
            max_edge = max(edges)

            if max_edge <= alpha:
                # Add boundary faces of this tetrahedron
                for i in range(4):
                    face = tuple(sorted([simplex[j] for j in range(4) if j != i]))
                    if face in kept_faces:
                        kept_faces.remove(face)  # Interior face (shared by 2 tets)
                    else:
                        kept_faces.add(face)

        faces_list = [list(f) for f in kept_faces]

        # Filter by max_edge_length
        filtered_faces = []
        for face in faces_list:
            face_pts = pts[face]
            e1 = np.linalg.norm(face_pts[1] - face_pts[0])
            e2 = np.linalg.norm(face_pts[2] - face_pts[0])
            e3 = np.linalg.norm(face_pts[2] - face_pts[1])
            if max(e1, e2, e3) <= max_edge_length:
                filtered_faces.append(face)

        # Compute envelope properties
        envelope_verts = pts.tolist()
        total_area = 0.0
        for face in filtered_faces:
            v0, v1, v2 = pts[face[0]], pts[face[1]], pts[face[2]]
            total_area += 0.5 * np.linalg.norm(np.cross(v1 - v0, v2 - v0))

        # Estimate volume via convex hull of kept vertices
        used_indices = list(set(idx for face in filtered_faces for idx in face))
        volume = 0.0
        if len(used_indices) >= 4:
            try:
                hull = ConvexHull(pts[used_indices])
                volume = float(hull.volume)
            except Exception:
                pass

        return {
            "envelope": {
                "vertices": envelope_verts,
                "faces": filtered_faces,
            },
            "metadata": {
                "n_input_points": len(pts),
                "n_envelope_vertices": len(used_indices),
                "n_envelope_faces": len(filtered_faces),
                "surface_area": round(total_area, 2),
                "estimated_volume": round(volume, 2),
                "alpha_param": alpha_param,
                "max_edge_length": max_edge_length,
                "anisotropy_ratio": anisotropy_ratio,
            },
        }

    except Exception as e:
        logger.exception(f"Envelope generation error: {e}")
        # Fallback: convex hull
        try:
            hull = ConvexHull(pts)
            return {
                "envelope": {
                    "vertices": pts.tolist(),
                    "faces": hull.simplices.tolist(),
                },
                "metadata": {
                    "n_input_points": len(pts),
                    "n_envelope_faces": len(hull.simplices),
                    "surface_area": round(float(hull.area), 2),
                    "estimated_volume": round(float(hull.volume), 2),
                    "fallback": "convex_hull",
                },
            }
        except Exception as e2:
            return {"error": f"Envelope generation failed: {str(e2)}"}


@router.post("/api/intervalmaker")
async def intervalmaker_main(request):
    """Main intervalmaker endpoint — dispatches based on 'action' field."""
    t0 = time.time()
    body = await request.json()
    action = body.get("action", "").lower()

    if action == "detect":
        result = _detect_intervals(body)
    elif action == "envelope":
        result = _envelope(body)
    else:
        return JSONResponse(status_code=400, content={
            "error": f"Unknown action '{action}'",
            "valid_actions": ["detect", "envelope"]
        })

    if isinstance(result, dict) and "error" in result and len(result) <= 2:
        return JSONResponse(status_code=400, content=result)

    result["processing_time_ms"] = int((time.time() - t0) * 1000)
    return result


@router.post("/api/intervalmaker/detect")
async def intervalmaker_detect(request):
    t0 = time.time()
    body = await request.json()
    result = _detect_intervals(body)
    if isinstance(result, dict) and "error" in result and len(result) <= 2:
        return JSONResponse(status_code=400, content=result)
    result["processing_time_ms"] = int((time.time() - t0) * 1000)
    return result


@router.post("/api/intervalmaker/envelope")
async def intervalmaker_envelope(request):
    t0 = time.time()
    body = await request.json()
    result = _envelope(body)
    if isinstance(result, dict) and "error" in result and len(result) <= 2:
        return JSONResponse(status_code=400, content=result)
    result["processing_time_ms"] = int((time.time() - t0) * 1000)
    return result
