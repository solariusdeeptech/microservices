"""
POST /sections — Génération de coupes géologiques 2D

Input JSON:
{
  "blocks": [{"x": ..., "y": ..., "z": ..., "grade": ...}, ...],
  "section_type": "cross",  // cross | long | plan
  "section_value": 500.0,   // coordinate of the section plane
  "color_by": "grade",
  "colormap": "viridis",
  "block_size": { "x": 10, "y": 10, "z": 5 }
}
"""

import io
import base64
import time

import numpy as np
import pyvista as pv
from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter()


class SectionRequest(BaseModel):
    blocks: list[dict]
    section_type: str = "cross"  # cross (YZ) | long (XZ) | plan (XY)
    section_value: float = 0.0
    color_by: str = "grade"
    colormap: str = "viridis"
    block_size: dict = {"x": 10, "y": 10, "z": 5}
    tolerance: float = 5.0  # distance tolerance for section


@router.post("/sections")
async def generate_section(req: SectionRequest):
    start = time.time()

    blocks = req.blocks
    dx = req.block_size.get("x", 10)
    dy = req.block_size.get("y", 10)
    dz = req.block_size.get("z", 5)

    # Filter blocks near section plane
    filtered = []
    for b in blocks:
        if req.section_type == "cross":  # YZ section at X=value
            if abs(b["x"] - req.section_value) <= req.tolerance:
                filtered.append(b)
        elif req.section_type == "long":  # XZ section at Y=value
            if abs(b["y"] - req.section_value) <= req.tolerance:
                filtered.append(b)
        elif req.section_type == "plan":  # XY section at Z=value
            if abs(b["z"] - req.section_value) <= req.tolerance:
                filtered.append(b)

    if not filtered:
        return {"error": "No blocks found at section plane", "section_type": req.section_type, "value": req.section_value}

    # Build 2D section image
    centers = np.array([[b["x"], b["y"], b["z"]] for b in filtered])
    values = np.array([b.get(req.color_by, 0) for b in filtered], dtype=float)

    # Create PyVista mesh
    points = pv.PolyData(centers)
    points[req.color_by] = values

    geom = pv.Cube(x_length=dx, y_length=dy, z_length=dz)
    glyphs = points.glyph(geom=geom, scale=False, orient=False)
    glyphs[req.color_by] = np.repeat(values, 1)

    # Render
    plotter = pv.Plotter(off_screen=True, window_size=[1920, 1080])
    plotter.add_mesh(glyphs, scalars=req.color_by, cmap=req.colormap)

    # Set camera for section view
    if req.section_type == "cross":
        plotter.view_yz()
    elif req.section_type == "long":
        plotter.view_xz()
    else:
        plotter.view_xy()

    plotter.reset_camera()

    img = plotter.screenshot(return_img=True)
    img_buffer = io.BytesIO()
    from PIL import Image
    Image.fromarray(img).save(img_buffer, format="PNG")
    img_buffer.seek(0)

    plotter.close()

    elapsed = round(time.time() - start, 3)

    return {
        "section_image_base64": base64.b64encode(img_buffer.read()).decode("utf-8"),
        "section_data": [
            {"x": b["x"], "y": b["y"], "z": b["z"], req.color_by: b.get(req.color_by, 0)}
            for b in filtered
        ],
        "metadata": {
            "section_type": req.section_type,
            "section_value": req.section_value,
            "blocks_in_section": len(filtered),
            "processing_time_s": elapsed,
            "engine": "PyVista/VTK",
        }
    }
