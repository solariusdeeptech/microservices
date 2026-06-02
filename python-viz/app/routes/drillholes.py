"""
POST /api/drillholes — Visualisation 3D de sondages

Supports two contract modes:

1. New contract (inline drillholes array):
   {drillholes: [{id, collar, surveys, intervals}], color_by, radius, format}

2. Legacy contract (separate arrays):
   {collars, surveys, intervals, color_by, colormap, tube_radius, output_format}
"""
import io
import base64
import time
import math

import numpy as np
import pyvista as pv
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response
from loguru import logger

router = APIRouter()


def compute_trajectory(collar: dict, surveys: list[dict]) -> list[tuple]:
    """Compute 3D trajectory using minimum curvature method."""
    x, y, z = collar.get("x", 0), collar.get("y", 0), collar.get("z", 0)
    trajectory = [(x, y, z)]

    if not surveys:
        depth = collar.get("depth", 100)
        trajectory.append((x, y, z - depth))
        return trajectory

    sorted_surveys = sorted(surveys, key=lambda s: s.get("depth", 0))

    for i in range(len(sorted_surveys) - 1):
        s1 = sorted_surveys[i]
        s2 = sorted_surveys[i + 1]
        dz = s2["depth"] - s1["depth"]
        if dz <= 0:
            continue

        az1 = math.radians(s1.get("azimuth", 0))
        dip1 = math.radians(s1.get("dip", -90))
        az2 = math.radians(s2.get("azimuth", 0))
        dip2 = math.radians(s2.get("dip", -90))

        # Minimum curvature
        cos_theta = (math.cos(dip2 - dip1) -
                     math.sin(dip1) * math.sin(dip2) * (1 - math.cos(az2 - az1)))
        cos_theta = max(-1, min(1, cos_theta))
        theta = math.acos(cos_theta)
        rf = 1.0 if abs(theta) < 1e-6 else (2 / theta) * math.tan(theta / 2)

        dx = 0.5 * dz * (math.sin(dip1) * math.sin(az1) + math.sin(dip2) * math.sin(az2)) * rf
        dy = 0.5 * dz * (math.sin(dip1) * math.cos(az1) + math.sin(dip2) * math.cos(az2)) * rf
        ddz = 0.5 * dz * (math.cos(dip1) + math.cos(dip2)) * rf

        x += dx
        y += dy
        z -= ddz
        trajectory.append((x, y, z))

    return trajectory


def _interpolate_value_at_depth(intervals: list[dict], depth: float, color_by: str) -> float | None:
    """Get interval value at a given depth."""
    for iv in intervals:
        if iv.get("from", 0) <= depth < iv.get("to", 0):
            return iv.get(color_by, iv.get("value", None))
    return None


def _render_drillholes(drillhole_data: list[dict], color_by: str, radius: float,
                        colormap: str, output_format: str) -> dict | Response:
    """Render drillholes with PyVista."""
    plotter = pv.Plotter(off_screen=True, window_size=[1920, 1080])

    all_values = []
    bounding_min = [float('inf')] * 3
    bounding_max = [float('-inf')] * 3
    total_intervals = 0
    total_length = 0.0

    for dh in drillhole_data:
        collar = dh.get("collar", {})
        surveys = dh.get("surveys", [])
        intervals = dh.get("intervals", [])
        hole_id = dh.get("id", "")

        trajectory = compute_trajectory(collar, surveys)
        if len(trajectory) < 2:
            continue

        points = np.array(trajectory)

        # Update bounding box
        for dim in range(3):
            bounding_min[dim] = min(bounding_min[dim], float(points[:, dim].min()))
            bounding_max[dim] = max(bounding_max[dim], float(points[:, dim].max()))

        # Compute total length
        for k in range(len(points) - 1):
            total_length += float(np.linalg.norm(points[k + 1] - points[k]))

        total_intervals += len(intervals)

        try:
            line = pv.Spline(points, n_points=max(10, len(points) * 5))
            tube = line.tube(radius=radius)

            if intervals:
                # Assign scalar values to tube points based on intervals
                n_pts = tube.n_points
                scalars = np.zeros(n_pts)
                depths = np.linspace(0, collar.get("depth", 100), n_pts)

                for idx, depth in enumerate(depths):
                    val = _interpolate_value_at_depth(intervals, depth, color_by)
                    if val is not None:
                        scalars[idx] = val
                        all_values.append(val)

                tube[color_by] = scalars
                plotter.add_mesh(tube, scalars=color_by, cmap=colormap)
            else:
                plotter.add_mesh(tube, color="gray")
        except Exception as e:
            logger.warning(f"Failed to render hole {hole_id}: {e}")
            continue

    plotter.reset_camera()

    metadata = {
        "num_drillholes": len(drillhole_data),
        "total_intervals": total_intervals,
        "total_length_m": round(total_length, 1),
        "bounding_box": {
            "min": [round(v, 1) for v in bounding_min],
            "max": [round(v, 1) for v in bounding_max],
        },
        "engine": "PyVista/VTK",
    }
    if all_values:
        metadata["color_range"] = {
            "min": round(min(all_values), 4),
            "max": round(max(all_values), 4),
        }

    if output_format == "gltf":
        buf = io.BytesIO()
        try:
            plotter.export_gltf(buf)
            buf.seek(0)
            plotter.close()
            return Response(content=buf.read(), media_type="model/gltf-binary")
        except Exception as e:
            logger.error(f"GLTF export failed: {e}")
            # Fallback to JSON metadata
            plotter.close()
            return metadata

    elif output_format == "png":
        img = plotter.screenshot(return_img=True)
        buf = io.BytesIO()
        from PIL import Image
        Image.fromarray(img).save(buf, format="PNG")
        buf.seek(0)
        plotter.close()
        return Response(content=buf.read(), media_type="image/png")

    else:  # json
        plotter.close()
        return metadata


@router.post("/api/drillholes")
async def render_drillholes(request: Request):
    """Drillhole visualization endpoint."""
    t0 = time.time()

    try:
        body = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content={"error": "Invalid JSON body"})

    color_by = body.get("color_by", "value")
    colormap = body.get("colormap", "hot")

    # Detect contract: new (has 'drillholes' array) vs legacy (has 'collars')
    if "drillholes" in body:
        # New contract: inline drillhole objects
        drillhole_data = body["drillholes"]
        radius = body.get("radius", 0.5)
        output_format = body.get("format", "json")
    elif "collars" in body:
        # Legacy contract: separate arrays
        collars = body.get("collars", [])
        surveys = body.get("surveys", [])
        intervals = body.get("intervals", [])
        radius = body.get("tube_radius", 2.0)
        output_format = body.get("output_format", body.get("format", "json"))

        # Convert to unified format
        drillhole_data = []
        for collar in collars:
            hole_id = collar.get("id", "")
            drillhole_data.append({
                "id": hole_id,
                "collar": collar,
                "surveys": [s for s in surveys if s.get("hole_id") == hole_id],
                "intervals": [iv for iv in intervals if iv.get("hole_id") == hole_id],
            })
    else:
        return JSONResponse(status_code=400, content={
            "error": "Invalid request. Provide either {drillholes: [...]} (new) or {collars, surveys, intervals} (legacy)."
        })

    if not drillhole_data:
        return JSONResponse(status_code=400, content={"error": "No drillhole data provided"})

    result = _render_drillholes(drillhole_data, color_by, radius, colormap, output_format)

    if isinstance(result, Response):
        return result

    elapsed_ms = int((time.time() - t0) * 1000)
    if isinstance(result, dict):
        result["compute_time_ms"] = elapsed_ms

    return result
