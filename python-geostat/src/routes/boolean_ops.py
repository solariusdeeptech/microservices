"""
POST /api/boolean-ops — CSG operations (union, intersection, difference) on 3D meshes.
Uses trimesh for mesh boolean operations.
"""
import time
import logging
import numpy as np
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)
router = APIRouter()


def _compute_mesh_properties(vertices: np.ndarray, faces: np.ndarray) -> dict:
    """Compute volume and surface area from vertices + triangular faces."""
    total_volume = 0.0
    total_area = 0.0

    for face in faces:
        v0 = vertices[face[0]]
        v1 = vertices[face[1]]
        v2 = vertices[face[2]]

        # Surface area (triangle area)
        edge1 = v1 - v0
        edge2 = v2 - v0
        cross = np.cross(edge1, edge2)
        total_area += 0.5 * np.linalg.norm(cross)

        # Signed volume contribution (divergence theorem)
        total_volume += np.dot(v0, cross) / 6.0

    return {
        "volume": abs(float(total_volume)),
        "surface_area": float(total_area),
        "n_vertices": len(vertices),
        "n_faces": len(faces),
    }


@router.post("/api/boolean-ops")
async def boolean_ops(request: Request):
    t0 = time.time()
    body = await request.json()

    operation = body.get("operation", "").lower()
    if operation not in ("union", "intersection", "difference"):
        return JSONResponse(
            status_code=400,
            content={"error": f"Invalid operation '{operation}'. Must be union, intersection, or difference."}
        )

    mesh_a = body.get("mesh_a", {})
    mesh_b = body.get("mesh_b", {})

    if not mesh_a.get("vertices") or not mesh_a.get("faces"):
        return JSONResponse(status_code=400, content={"error": "mesh_a must have vertices and faces"})
    if not mesh_b.get("vertices") or not mesh_b.get("faces"):
        return JSONResponse(status_code=400, content={"error": "mesh_b must have vertices and faces"})

    try:
        import trimesh

        verts_a = np.array(mesh_a["vertices"], dtype=float)
        faces_a = np.array(mesh_a["faces"], dtype=int)
        verts_b = np.array(mesh_b["vertices"], dtype=float)
        faces_b = np.array(mesh_b["faces"], dtype=int)

        tm_a = trimesh.Trimesh(vertices=verts_a, faces=faces_a, process=True)
        tm_b = trimesh.Trimesh(vertices=verts_b, faces=faces_b, process=True)

        # Compute properties of inputs
        props_a = _compute_mesh_properties(verts_a, faces_a)
        props_b = _compute_mesh_properties(verts_b, faces_b)

        # Perform boolean operation
        if operation == "union":
            result_mesh = tm_a.union(tm_b)
        elif operation == "intersection":
            result_mesh = tm_a.intersection(tm_b)
        elif operation == "difference":
            result_mesh = tm_a.difference(tm_b)

        result_verts = result_mesh.vertices.tolist()
        result_faces = result_mesh.faces.tolist()

        props_result = _compute_mesh_properties(
            np.array(result_verts), np.array(result_faces)
        )

        elapsed_ms = int((time.time() - t0) * 1000)

        return {
            "operation": operation,
            "result": {
                "vertices": result_verts,
                "faces": result_faces,
            },
            "metadata": {
                "result": props_result,
                "input_a": props_a,
                "input_b": props_b,
            },
            "is_watertight": bool(result_mesh.is_watertight),
            "processing_time_ms": elapsed_ms,
            "engine": "trimesh",
        }

    except ImportError:
        # Fallback: manual CSG approximation without trimesh boolean engine
        logger.warning("trimesh boolean engine not available, using basic merge")
        verts_a = np.array(mesh_a["vertices"], dtype=float)
        faces_a = np.array(mesh_a["faces"], dtype=int)
        verts_b = np.array(mesh_b["vertices"], dtype=float)
        faces_b = np.array(mesh_b["faces"], dtype=int)

        if operation == "union":
            # Naive union: concatenate meshes
            offset = len(verts_a)
            combined_verts = np.vstack([verts_a, verts_b])
            combined_faces = np.vstack([faces_a, faces_b + offset])
        elif operation == "intersection":
            return JSONResponse(
                status_code=501,
                content={"error": "Intersection requires trimesh boolean engine (manifold3d). Install with: pip install manifold3d"}
            )
        elif operation == "difference":
            return JSONResponse(
                status_code=501,
                content={"error": "Difference requires trimesh boolean engine (manifold3d). Install with: pip install manifold3d"}
            )
        else:
            combined_verts = verts_a
            combined_faces = faces_a

        props_result = _compute_mesh_properties(combined_verts, combined_faces)
        elapsed_ms = int((time.time() - t0) * 1000)

        return {
            "operation": operation,
            "result": {
                "vertices": combined_verts.tolist(),
                "faces": combined_faces.tolist(),
            },
            "metadata": {
                "result": props_result,
                "input_a": _compute_mesh_properties(verts_a, faces_a),
                "input_b": _compute_mesh_properties(verts_b, faces_b),
            },
            "is_watertight": False,
            "processing_time_ms": elapsed_ms,
            "engine": "fallback-merge",
            "warning": "Boolean engine not available. Union is a naive merge. Install manifold3d for proper CSG.",
        }

    except Exception as e:
        logger.exception(f"Boolean ops error: {e}")
        return JSONResponse(
            status_code=500,
            content={"error": str(e), "operation": operation}
        )
