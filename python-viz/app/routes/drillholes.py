"""
POST /drillholes — Visualisation 3D de sondages

Input JSON:
{
  "collars": [{"id": "DH001", "x": 100, "y": 200, "z": 500, "depth": 150}, ...],
  "surveys": [{"hole_id": "DH001", "depth": 0, "azimuth": 90, "dip": -60}, ...],
  "intervals": [{"hole_id": "DH001", "from": 0, "to": 10, "grade": 2.5}, ...],
  "color_by": "grade",
  "colormap": "hot",
  "tube_radius": 2.0,
  "output_format": "gltf"
}
"""

import io
import base64
import time
import math

import numpy as np
import pyvista as pv
from fastapi import APIRouter
from pydantic import BaseModel
from loguru import logger

router = APIRouter()


class DrillholeRequest(BaseModel):
    collars: list[dict]
    surveys: list[dict] = []
    intervals: list[dict] = []
    color_by: str = "grade"
    colormap: str = "hot"
    tube_radius: float = 2.0
    output_format: str = "gltf"  # gltf | png


def compute_trajectory(collar: dict, surveys: list[dict]) -> list[tuple]:
    """Calcule la trajectoire 3D d'un sondage à partir des mesures de déviation."""
    x, y, z = collar["x"], collar["y"], collar["z"]
    trajectory = [(x, y, z)]

    if not surveys:
        # Vertical hole
        depth = collar.get("depth", 100)
        trajectory.append((x, y, z - depth))
        return trajectory

    sorted_surveys = sorted(surveys, key=lambda s: s["depth"])

    for i in range(len(sorted_surveys) - 1):
        s1 = sorted_surveys[i]
        s2 = sorted_surveys[i + 1]
        dz = s2["depth"] - s1["depth"]

        az1 = math.radians(s1["azimuth"])
        dip1 = math.radians(s1["dip"])
        az2 = math.radians(s2["azimuth"])
        dip2 = math.radians(s2["dip"])

        # Minimum curvature method
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


@router.post("/drillholes")
async def render_drillholes(req: DrillholeRequest):
    start = time.time()
    logger.info(f"Drillhole render: {len(req.collars)} collars")

    plotter = pv.Plotter(off_screen=True, window_size=[1920, 1080])

    for collar in req.collars:
        hole_id = collar["id"]

        # Get surveys for this hole
        hole_surveys = [s for s in req.surveys if s.get("hole_id") == hole_id]
        trajectory = compute_trajectory(collar, hole_surveys)

        if len(trajectory) < 2:
            continue

        # Create tube from trajectory
        points = np.array(trajectory)
        line = pv.Spline(points, n_points=max(10, len(points) * 5))
        tube = line.tube(radius=req.tube_radius)

        # Get grade values along intervals
        hole_intervals = [iv for iv in req.intervals if iv.get("hole_id") == hole_id]
        if hole_intervals:
            avg_grade = np.mean([iv.get(req.color_by, 0) for iv in hole_intervals])
            tube[req.color_by] = np.full(tube.n_points, avg_grade)
            plotter.add_mesh(tube, scalars=req.color_by, cmap=req.colormap)
        else:
            plotter.add_mesh(tube, color="gray")

    plotter.reset_camera()

    result = {"metadata": {"num_holes": len(req.collars)}}

    if req.output_format == "gltf":
        buf = io.BytesIO()
        plotter.export_gltf(buf)
        buf.seek(0)
        result["gltf_base64"] = base64.b64encode(buf.read()).decode("utf-8")
    else:
        img = plotter.screenshot(return_img=True)
        buf = io.BytesIO()
        from PIL import Image
        Image.fromarray(img).save(buf, format="PNG")
        buf.seek(0)
        result["image_base64"] = base64.b64encode(buf.read()).decode("utf-8")

    plotter.close()
    elapsed = round(time.time() - start, 3)
    result["metadata"]["processing_time_s"] = elapsed
    result["metadata"]["engine"] = "PyVista/VTK"

    return result
