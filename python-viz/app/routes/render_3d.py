"""
POST /render-3d — Rendu 3D de modèles de blocs

Exporte en glTF ou image PNG pour affichage dans le navigateur.

Input JSON:
{
  "blocks": [
    { "x": 0, "y": 0, "z": 0, "grade": 1.5, "in_pit": true },
    ...
  ],
  "block_size": { "x": 10, "y": 10, "z": 5 },
  "color_by": "grade",
  "colormap": "viridis",
  "output_format": "gltf",  // gltf | png | both
  "camera": {
    "position": [100, 100, 200],
    "focal_point": [50, 50, 0],
    "up": [0, 0, 1]
  },
  "options": {
    "show_edges": false,
    "opacity": 1.0,
    "clim": [0, 5]
  }
}
"""

import io
import base64
import time
from typing import Optional

import numpy as np
import pyvista as pv
from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter()


class BlockSize(BaseModel):
    x: float = 10.0
    y: float = 10.0
    z: float = 5.0


class Camera(BaseModel):
    position: list[float] = [100, 100, 200]
    focal_point: list[float] = [50, 50, 0]
    up: list[float] = [0, 0, 1]


class RenderOptions(BaseModel):
    show_edges: bool = False
    opacity: float = 1.0
    clim: Optional[list[float]] = None


class Render3DRequest(BaseModel):
    blocks: list[dict]
    block_size: BlockSize = BlockSize()
    color_by: str = "grade"
    colormap: str = "viridis"
    output_format: str = "gltf"  # gltf | png | both
    camera: Camera = Camera()
    options: RenderOptions = RenderOptions()


@router.post("/render-3d")
async def render_3d(req: Render3DRequest):
    start = time.time()

    blocks = req.blocks
    n = len(blocks)

    if n == 0:
        return {"error": "No blocks provided"}

    # Build block mesh using PyVista
    centers = np.array([[b["x"], b["y"], b["z"]] for b in blocks])
    values = np.array([b.get(req.color_by, 0) for b in blocks], dtype=float)

    # Create individual block meshes and combine
    dx, dy, dz = req.block_size.x, req.block_size.y, req.block_size.z

    # Use UnstructuredGrid for efficiency with many blocks
    grid = pv.RectilinearGrid(
        np.unique(centers[:, 0]),
        np.unique(centers[:, 1]),
        np.unique(centers[:, 2]),
    )

    # Alternative: create voxels
    points = pv.PolyData(centers)
    points[req.color_by] = values
    voxels = points.glyph(
        geom=pv.Cube(x_length=dx, y_length=dy, z_length=dz),
        scale=False,
        orient=False,
    )
    voxels[req.color_by] = np.repeat(values, 1)  # Map values to glyphs

    result = {
        "metadata": {
            "num_blocks": n,
            "processing_time_s": 0,
            "engine": "PyVista/VTK",
        }
    }

    # Export glTF
    if req.output_format in ("gltf", "both"):
        plotter = pv.Plotter(off_screen=True)
        clim = req.options.clim or [float(values.min()), float(values.max())]
        plotter.add_mesh(
            voxels,
            scalars=req.color_by,
            cmap=req.colormap,
            show_edges=req.options.show_edges,
            opacity=req.options.opacity,
            clim=clim,
        )
        plotter.camera_position = [
            tuple(req.camera.position),
            tuple(req.camera.focal_point),
            tuple(req.camera.up),
        ]

        # Export to glTF buffer
        gltf_buffer = io.BytesIO()
        plotter.export_gltf(gltf_buffer)
        gltf_buffer.seek(0)
        result["gltf_base64"] = base64.b64encode(gltf_buffer.read()).decode("utf-8")
        plotter.close()

    # Export PNG
    if req.output_format in ("png", "both"):
        plotter = pv.Plotter(off_screen=True, window_size=[1920, 1080])
        clim = req.options.clim or [float(values.min()), float(values.max())]
        plotter.add_mesh(
            voxels,
            scalars=req.color_by,
            cmap=req.colormap,
            show_edges=req.options.show_edges,
            opacity=req.options.opacity,
            clim=clim,
        )
        plotter.camera_position = [
            tuple(req.camera.position),
            tuple(req.camera.focal_point),
            tuple(req.camera.up),
        ]

        img = plotter.screenshot(return_img=True)
        img_buffer = io.BytesIO()
        from PIL import Image
        Image.fromarray(img).save(img_buffer, format="PNG")
        img_buffer.seek(0)
        result["image_base64"] = base64.b64encode(img_buffer.read()).decode("utf-8")
        plotter.close()

    elapsed = round(time.time() - start, 3)
    result["metadata"]["processing_time_s"] = elapsed

    return result
