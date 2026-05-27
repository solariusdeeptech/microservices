"""
GET /health — Health check
"""

import sys
from datetime import datetime
from fastapi import APIRouter

router = APIRouter()


@router.get("/health")
async def health_check():
    try:
        import pyvista as pv
        pyvista_ok = True
        vtk_version = pv.vtk_version
    except Exception:
        pyvista_ok = False
        vtk_version = None

    return {
        "status": "healthy",
        "service": "python-viz",
        "version": "1.0.0",
        "python_version": sys.version,
        "timestamp": datetime.utcnow().isoformat(),
        "packages": {
            "pyvista": pyvista_ok,
            "vtk": vtk_version,
        },
        "capabilities": [
            "render_3d_gltf",
            "render_3d_image",
            "mps_simulation",
            "geological_sections",
            "drillhole_visualization",
        ]
    }
