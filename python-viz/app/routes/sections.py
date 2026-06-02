"""
POST /api/sections — Génération de coupes géologiques 2D/3D

Supports two contract modes:

1. New contract (start/end section line):
   {start, end, width, surfaces, drillholes, block_model, format}

2. Legacy contract (block list + section_type):
   {blocks, section_type, section_value, color_by, colormap, block_size}
"""
import io
import base64
import time
import math

import numpy as np
import pyvista as pv
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel
from loguru import logger

router = APIRouter()


# ========== Legacy contract ==========

class LegacySectionRequest(BaseModel):
    blocks: list[dict]
    section_type: str = "cross"
    section_value: float = 0.0
    color_by: str = "grade"
    colormap: str = "viridis"
    block_size: dict = {"x": 10, "y": 10, "z": 5}
    tolerance: float = 5.0


def _legacy_section(req: LegacySectionRequest) -> dict:
    """Original section logic: filter blocks by plane, render image."""
    blocks = req.blocks
    dx = req.block_size.get("x", 10)
    dy = req.block_size.get("y", 10)
    dz = req.block_size.get("z", 5)

    filtered = []
    for b in blocks:
        if req.section_type == "cross":
            if abs(b["x"] - req.section_value) <= req.tolerance:
                filtered.append(b)
        elif req.section_type == "long":
            if abs(b["y"] - req.section_value) <= req.tolerance:
                filtered.append(b)
        elif req.section_type == "plan":
            if abs(b["z"] - req.section_value) <= req.tolerance:
                filtered.append(b)

    if not filtered:
        return {"error": "No blocks found at section plane", "section_type": req.section_type, "value": req.section_value}

    centers = np.array([[b["x"], b["y"], b["z"]] for b in filtered])
    values = np.array([b.get(req.color_by, 0) for b in filtered], dtype=float)

    points = pv.PolyData(centers)
    points[req.color_by] = values
    geom = pv.Cube(x_length=dx, y_length=dy, z_length=dz)
    glyphs = points.glyph(geom=geom, scale=False, orient=False)
    glyphs[req.color_by] = np.repeat(values, 1)

    plotter = pv.Plotter(off_screen=True, window_size=[1920, 1080])
    plotter.add_mesh(glyphs, scalars=req.color_by, cmap=req.colormap)
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
            "engine": "PyVista/VTK",
        }
    }


# ========== New contract ==========

def _project_point_on_line(point: np.ndarray, line_start: np.ndarray, line_dir: np.ndarray, line_length: float) -> tuple:
    """Project a 3D point onto a section line. Returns (distance_along, distance_from_line)."""
    v = point - line_start
    along = float(np.dot(v, line_dir))
    proj = line_start + along * line_dir
    perp_dist = float(np.linalg.norm(point - proj))
    return along, perp_dist


def _intersect_surface_with_section(surface: dict, line_start: np.ndarray, line_dir: np.ndarray,
                                     line_normal: np.ndarray, width: float, line_length: float) -> list:
    """Intersect a triangulated surface with a vertical section plane."""
    vertices = np.array(surface.get("vertices", []), dtype=float)
    faces = np.array(surface.get("faces", []), dtype=int)

    if len(vertices) == 0 or len(faces) == 0:
        return []

    profile_points = []

    for face in faces:
        tri = vertices[face]
        # Check if triangle is near the section plane (within width/2)
        dists_to_plane = (tri - line_start) @ line_normal
        if np.all(np.abs(dists_to_plane) > width / 2):
            continue

        # Find intersection edges
        for i in range(3):
            j = (i + 1) % 3
            d1, d2 = dists_to_plane[i], dists_to_plane[j]
            if d1 * d2 < 0:  # Edge crosses plane
                t = d1 / (d1 - d2)
                intersect = tri[i] + t * (tri[j] - tri[i])
                along, perp = _project_point_on_line(intersect, line_start, line_dir, line_length)
                if 0 <= along <= line_length:
                    profile_points.append([round(along, 2), round(float(intersect[2]), 2)])
            elif abs(d1) <= width / 2:
                along, perp = _project_point_on_line(tri[i], line_start, line_dir, line_length)
                if 0 <= along <= line_length and perp <= width / 2:
                    profile_points.append([round(along, 2), round(float(tri[i][2]), 2)])

    # Sort by distance along section line
    profile_points.sort(key=lambda p: p[0])

    return profile_points


def _new_section(body: dict) -> dict | Response:
    """New section contract: start/end line, surfaces, drillholes, block_model."""
    start = body.get("start", {})
    end = body.get("end", {})
    width = body.get("width", 50)
    surfaces = body.get("surfaces", [])
    drillholes = body.get("drillholes", [])
    block_model = body.get("block_model", None)
    output_format = body.get("format", "json")

    line_start = np.array([start.get("x", 0), start.get("y", 0), start.get("z", 0)], dtype=float)
    line_end = np.array([end.get("x", 0), end.get("y", 0), end.get("z", 0)], dtype=float)
    line_vec = line_end - line_start
    line_length = float(np.linalg.norm(line_vec))

    if line_length < 1e-6:
        return {"error": "Section line has zero length"}

    line_dir = line_vec / line_length
    # Normal to the section plane (horizontal perpendicular)
    line_normal = np.array([-line_dir[1], line_dir[0], 0], dtype=float)
    norm_len = np.linalg.norm(line_normal)
    if norm_len > 1e-6:
        line_normal /= norm_len
    else:
        line_normal = np.array([1, 0, 0], dtype=float)

    result = {
        "section_line": {
            "start": [float(line_start[0]), float(line_start[1]), float(line_start[2])],
            "end": [float(line_end[0]), float(line_end[1]), float(line_end[2])],
            "length": round(line_length, 2),
            "azimuth": round(float(math.degrees(math.atan2(line_dir[0], line_dir[1]))) % 360, 1),
            "width": width,
        },
    }

    # 1. Intersect surfaces
    intersected_surfaces = []
    for surface in surfaces:
        profile = _intersect_surface_with_section(
            surface, line_start, line_dir, line_normal, width, line_length
        )
        intersected_surfaces.append({
            "id": surface.get("id", "unknown"),
            "profile_2d": profile,
            "n_points": len(profile),
        })
    result["intersected_surfaces"] = intersected_surfaces

    # 2. Project drillholes onto section (use 2D horizontal distance for band check)
    projected_drillholes = []
    for dh in drillholes:
        collar = dh.get("collar", {})
        collar_pt_2d = np.array([collar.get("x", 0), collar.get("y", 0), 0], dtype=float)
        line_start_2d = np.array([line_start[0], line_start[1], 0], dtype=float)
        line_dir_2d = np.array([line_dir[0], line_dir[1], 0], dtype=float)
        ld_norm = np.linalg.norm(line_dir_2d)
        if ld_norm > 1e-6:
            line_dir_2d /= ld_norm

        along, perp_dist = _project_point_on_line(collar_pt_2d, line_start_2d, line_dir_2d, line_length)

        if perp_dist <= width / 2 and 0 <= along <= line_length:
            projected = {
                "id": dh.get("id", ""),
                "distance_along": round(along, 2),
                "distance_from_section": round(perp_dist, 2),
                "collar_z": collar.get("z", 0),
            }
            # Project intervals
            if "intervals" in dh:
                projected["intervals"] = dh["intervals"]
            projected_drillholes.append(projected)

    result["projected_drillholes"] = projected_drillholes

    # 3. Extract blocks within section band
    section_block_values = []
    if block_model:
        bm = block_model
        origin = bm.get("origin", {})
        size = bm.get("size", {})
        spacing = bm.get("spacing", {})
        values = bm.get("values", [])

        ox = origin.get("x", 0)
        oy = origin.get("y", 0)
        oz = origin.get("z", 0)
        nx = size.get("nx", 0)
        ny = size.get("ny", 0)
        nz = size.get("nz", 0)
        dx = spacing.get("dx", 10)
        dy = spacing.get("dy", 10)
        dz = spacing.get("dz", 5)

        for iz in range(nz):
            for iy in range(ny):
                for ix in range(nx):
                    idx = ix + iy * nx + iz * nx * ny
                    if idx >= len(values):
                        break

                    bx = ox + (ix + 0.5) * dx
                    by = oy + (iy + 0.5) * dy
                    bz = oz + (iz + 0.5) * dz

                    pt = np.array([bx, by, bz])
                    along, perp = _project_point_on_line(pt, line_start, line_dir, line_length)

                    if perp <= width / 2 and 0 <= along <= line_length:
                        section_block_values.append({
                            "distance_along": round(along, 2),
                            "z": round(bz, 2),
                            "value": values[idx],
                        })

    result["section_blocks"] = section_block_values
    result["n_section_blocks"] = len(section_block_values)

    # 4. Handle output formats
    if output_format in ("png", "gltf", "svg"):
        return _render_section(result, output_format)

    return result


def _render_section(section_data: dict, fmt: str) -> Response:
    """Render section to image/gltf/svg."""
    plotter = pv.Plotter(off_screen=True, window_size=[1920, 800])

    # Plot surfaces as polylines
    for surf in section_data.get("intersected_surfaces", []):
        pts_2d = surf.get("profile_2d", [])
        if len(pts_2d) >= 2:
            pts_3d = np.array([[p[0], 0, p[1]] for p in pts_2d])
            line = pv.Spline(pts_3d, n_points=max(10, len(pts_3d) * 3))
            plotter.add_mesh(line, color="white", line_width=3)

    # Plot drillholes as vertical lines
    for dh in section_data.get("projected_drillholes", []):
        x = dh["distance_along"]
        z_top = dh.get("collar_z", 0)
        intervals = dh.get("intervals", [])
        if intervals:
            z_bottom = z_top - max(iv.get("to", 0) for iv in intervals)
        else:
            z_bottom = z_top - 100

        line = pv.Line([x, 0, z_top], [x, 0, z_bottom])
        tube = line.tube(radius=1)
        plotter.add_mesh(tube, color="yellow")

    # Plot blocks as colored rectangles
    blocks = section_data.get("section_blocks", [])
    if blocks:
        pts = np.array([[b["distance_along"], 0, b["z"]] for b in blocks])
        vals = np.array([b["value"] for b in blocks])
        cloud = pv.PolyData(pts)
        cloud["value"] = vals
        geom = pv.Cube(x_length=10, y_length=1, z_length=5)
        glyphs = cloud.glyph(geom=geom, scale=False, orient=False)
        glyphs["value"] = np.repeat(vals, 1)
        plotter.add_mesh(glyphs, scalars="value", cmap="viridis")

    plotter.view_xz()
    plotter.reset_camera()

    if fmt == "gltf":
        buf = io.BytesIO()
        plotter.export_gltf(buf)
        buf.seek(0)
        plotter.close()
        return Response(content=buf.read(), media_type="model/gltf-binary")
    elif fmt == "svg":
        # SVG not natively supported by PyVista, return PNG
        img = plotter.screenshot(return_img=True)
        buf = io.BytesIO()
        from PIL import Image
        Image.fromarray(img).save(buf, format="PNG")
        buf.seek(0)
        plotter.close()
        return Response(content=buf.read(), media_type="image/png")
    else:  # png
        img = plotter.screenshot(return_img=True)
        buf = io.BytesIO()
        from PIL import Image
        Image.fromarray(img).save(buf, format="PNG")
        buf.seek(0)
        plotter.close()
        return Response(content=buf.read(), media_type="image/png")


# ========== Route handler ==========

@router.post("/api/sections")
async def generate_section(request: Request):
    t0 = time.time()

    try:
        body = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content={"error": "Invalid JSON body"})

    # Detect contract: new (has 'start'/'end') vs legacy (has 'blocks')
    if "start" in body and "end" in body:
        result = _new_section(body)
    elif "blocks" in body:
        try:
            req = LegacySectionRequest(**body)
            result = _legacy_section(req)
        except Exception as e:
            return JSONResponse(status_code=400, content={"error": str(e)})
    else:
        return JSONResponse(status_code=400, content={
            "error": "Invalid request. Provide either {start, end} (new) or {blocks, section_type} (legacy)."
        })

    # If result is a Response (binary format), return directly
    if isinstance(result, Response):
        return result

    elapsed_ms = int((time.time() - t0) * 1000)
    if isinstance(result, dict):
        result["compute_time_ms"] = elapsed_ms
        result["engine"] = "PyVista/VTK"

    return result
