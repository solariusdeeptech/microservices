"""
GeoDriver Routes — Module à intégrer dans python-geostat existant

Usage dans votre app.py :
    from geodriver_routes import geodriver_router
    app.include_router(geodriver_router)

Endpoints ajoutés :
    POST /api/spatial-continuity
    POST /api/hybrid-clustering
    POST /api/envelope-geometry
"""

import time
import logging
from typing import Optional

import numpy as np
from scipy.spatial import cKDTree, ConvexHull
from sklearn.cluster import KMeans
from sklearn.metrics import silhouette_score, silhouette_samples
from sklearn.preprocessing import MinMaxScaler

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

logger = logging.getLogger("geodriver")

geodriver_router = APIRouter(tags=["geodriver"])

# ═══════════════════════════════════════════════════════════════════════════
# Pydantic Models
# ═══════════════════════════════════════════════════════════════════════════

class CompositePoint(BaseModel):
    x: float
    y: float
    z: float
    grade: float

class CollarPoint(BaseModel):
    x: float
    y: float

class EllipsoidResult(BaseModel):
    center: dict
    axes: list[float]
    strike: float
    dip: float
    plunge: float
    anisotropyRatio: float
    sampleCount: int
    confidence: float

# --- Spatial Continuity ---
class SpatialContinuityRequest(BaseModel):
    projectId: str = ""
    windowSize: float = 50.0
    minSamples: int = 8
    composites: list[CompositePoint]
    collars: list[CollarPoint]

class SpatialContinuityResponse(BaseModel):
    ellipsoids: list[EllipsoidResult]
    compositeCount: int
    dominantStrike: float
    dominantDip: float
    avgAnisotropyRatio: float
    drillholeSpacing: float
    computeTimeMs: int

# --- Hybrid Clustering ---
class HybridClusteringRequest(BaseModel):
    projectId: str = ""
    alpha: float = 0.5
    sigma: float = 50.0
    kNeighbors: int = 8
    nClusters: Optional[int] = None
    composites: list[CompositePoint]
    features: dict[str, list[float]]

class DomainResult(BaseModel):
    id: str
    name: str
    code: str
    color: str
    count: int
    avgGrade: float
    gradeRange: dict
    silhouetteScore: float
    confidence: float
    centroid: dict
    ellipsoid: Optional[EllipsoidResult] = None

class HybridClusteringResponse(BaseModel):
    domains: list[DomainResult]
    overallSilhouette: float
    compositeCount: int
    features: list[str]
    alpha: float
    computeTimeMs: int

# --- Envelope Geometry ---
class DomainPoints(BaseModel):
    id: str
    points: list[CompositePoint]
    isMineralized: bool = True

class EnvelopeGeometryRequest(BaseModel):
    projectId: str = ""
    mode: str = "strict"
    confidenceDecay: str = "linear"
    spacing: float
    domains: list[DomainPoints]

class ConstrainedDomainResult(BaseModel):
    id: str
    volume: float
    dataVolume: float
    extrapolatedVolume: float
    vertexCount: int
    faceCount: int

class VolumeReport(BaseModel):
    totalVolume: float
    dataVolume: float
    extrapolatedVolume: float
    extrapolationPct: float
    maxExtensionUsed: float
    spacingUsed: float
    mode: str

class EnvelopeGeometryResponse(BaseModel):
    constrainedDomains: list[ConstrainedDomainResult]
    volumeReport: VolumeReport
    computeTimeMs: int


# ═══════════════════════════════════════════════════════════════════════════
# Constants
# ═══════════════════════════════════════════════════════════════════════════

ENVELOPE_LIMITS = {
    "strict":   {"mineralized": 1.0, "unmineralized": 0.25},
    "standard": {"mineralized": 1.0, "unmineralized": 0.50},
}

DOMAIN_COLORS = [
    "#FFC247", "#FF9149", "#E85D3A", "#D4975A", "#8B7355",
    "#FF6B6B", "#E8B84D", "#C7763E", "#A05A3C", "#FFD54F",
]

DOMAIN_NAMES = [
    "Domaine A", "Domaine B", "Domaine C", "Domaine D", "Domaine E",
    "Domaine F", "Domaine G", "Domaine H", "Domaine I", "Domaine J",
]


# ═══════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════

def _compute_drillhole_spacing(collars: list[CollarPoint]) -> float:
    """Median nearest-neighbor distance in XY."""
    if len(collars) < 2:
        return 100.0
    pts = np.array([[c.x, c.y] for c in collars])
    tree = cKDTree(pts)
    dists, _ = tree.query(pts, k=2)
    return float(np.median(dists[:, 1]))


def _pca_ellipsoid(pts: np.ndarray) -> Optional[EllipsoidResult]:
    """PCA on a set of 3D points → ellipsoid parameters."""
    if len(pts) < 4:
        return None

    center = pts.mean(axis=0)
    centered = pts - center
    cov = np.cov(centered.T)

    eigenvalues, eigenvectors = np.linalg.eigh(cov)
    idx = np.argsort(eigenvalues)[::-1]
    eigenvalues = eigenvalues[idx]
    eigenvectors = eigenvectors[:, idx]

    if eigenvalues[-1] <= 0:
        return None

    axes = [float(np.sqrt(max(ev, 0)) * 2) for ev in eigenvalues]
    anisotropy = float(np.sqrt(eigenvalues[0] / max(eigenvalues[2], 1e-10)))

    v = eigenvectors[:, 0]
    vx, vy, vz = v
    horizontal = np.sqrt(vx**2 + vy**2)
    strike = float(np.degrees(np.arctan2(vx, vy))) % 360
    dip = float(np.degrees(np.arctan2(abs(vz), horizontal)))
    plunge = float(np.degrees(np.arctan2(-vz, horizontal)))

    eigen_sep = float((eigenvalues[0] - eigenvalues[2]) / (eigenvalues[0] + 1e-10))
    density_factor = min(len(pts) / 24.0, 1.0)
    confidence = min(eigen_sep * density_factor, 1.0)

    return EllipsoidResult(
        center={"x": round(float(center[0]), 2), "y": round(float(center[1]), 2), "z": round(float(center[2]), 2)},
        axes=[round(a, 2) for a in axes],
        strike=round(strike, 1),
        dip=round(dip, 1),
        plunge=round(plunge, 1),
        anisotropyRatio=round(anisotropy, 2),
        sampleCount=len(pts),
        confidence=round(confidence, 2),
    )


# ═══════════════════════════════════════════════════════════════════════════
# Routes
# ═══════════════════════════════════════════════════════════════════════════

@geodriver_router.post("/api/spatial-continuity", response_model=SpatialContinuityResponse)
async def spatial_continuity(req: SpatialContinuityRequest):
    """Analyse de continuité spatiale locale (PCA sur fenêtres glissantes 3D)."""
    t0 = time.time()

    pts = np.array([[c.x, c.y, c.z] for c in req.composites])
    n = len(pts)
    if n < 10:
        raise HTTPException(400, "Pas assez de composites (min. 10)")

    window = req.windowSize
    min_samples = req.minSamples
    step = window * 0.5

    mins = pts.min(axis=0)
    maxs = pts.max(axis=0)

    ellipsoids = []
    tree = cKDTree(pts)

    wx = mins[0]
    while wx <= maxs[0]:
        wy = mins[1]
        while wy <= maxs[1]:
            wz = mins[2]
            while wz <= maxs[2]:
                center = np.array([wx + window / 2, wy + window / 2, wz + window / 2])
                candidates = tree.query_ball_point(center, r=window * 0.866)
                window_pts = []
                for idx in candidates:
                    p = pts[idx]
                    if (wx <= p[0] < wx + window and
                        wy <= p[1] < wy + window and
                        wz <= p[2] < wz + window):
                        window_pts.append(p)

                if len(window_pts) >= min_samples:
                    arr = np.array(window_pts)
                    ell = _pca_ellipsoid(arr)
                    if ell:
                        ellipsoids.append(ell)

                wz += step
            wy += step
        wx += step

    if ellipsoids:
        weights = np.array([e.confidence for e in ellipsoids])
        total_w = weights.sum()
        if total_w > 0:
            dom_strike = round(float(sum(e.strike * e.confidence for e in ellipsoids) / total_w), 1)
            dom_dip = round(float(sum(e.dip * e.confidence for e in ellipsoids) / total_w), 1)
        else:
            dom_strike = dom_dip = 0.0
        avg_aniso = round(float(np.mean([e.anisotropyRatio for e in ellipsoids])), 2)
    else:
        dom_strike = dom_dip = 0.0
        avg_aniso = 1.0

    spacing = _compute_drillhole_spacing(req.collars)

    elapsed = int((time.time() - t0) * 1000)
    logger.info(f"Spatial continuity: {len(ellipsoids)} ellipsoids, {n} composites, {elapsed}ms")

    return SpatialContinuityResponse(
        ellipsoids=ellipsoids,
        compositeCount=n,
        dominantStrike=dom_strike,
        dominantDip=dom_dip,
        avgAnisotropyRatio=avg_aniso,
        drillholeSpacing=round(spacing, 1),
        computeTimeMs=elapsed,
    )


@geodriver_router.post("/api/hybrid-clustering", response_model=HybridClusteringResponse)
async def hybrid_clustering(req: HybridClusteringRequest):
    """Clustering hybride géochimie + spatial (KMeans++ pondéré)."""
    t0 = time.time()

    n = len(req.composites)
    if n < 10:
        raise HTTPException(400, "Pas assez de composites (min. 10)")

    alpha = max(0.0, min(1.0, req.alpha))

    spatial = np.array([[c.x, c.y, c.z] for c in req.composites])
    grades = np.array([c.grade for c in req.composites])

    geochem_keys = list(req.features.keys())
    if not geochem_keys:
        geochem_keys = ["grade"]
        req.features["grade"] = [c.grade for c in req.composites]

    geochem = np.column_stack([np.array(req.features[k]) for k in geochem_keys])

    scaler_s = MinMaxScaler()
    scaler_g = MinMaxScaler()
    norm_spatial = scaler_s.fit_transform(spatial)
    norm_geochem = scaler_g.fit_transform(geochem)

    w_spatial = (1 - alpha)
    w_geochem = alpha / max(len(geochem_keys), 1)
    feature_matrix = np.hstack([
        norm_spatial * w_spatial,
        norm_geochem * w_geochem,
    ])

    if req.nClusters is not None and req.nClusters >= 2:
        k = min(req.nClusters, n // 3, 10)
    else:
        best_k, best_score = 2, -1.0
        max_k = min(6, n // 5)
        for try_k in range(2, max(3, max_k + 1)):
            km = KMeans(n_clusters=try_k, init="k-means++", n_init=5, max_iter=50, random_state=42)
            labels = km.fit_predict(feature_matrix)
            if len(set(labels)) < 2:
                continue
            score = float(silhouette_score(feature_matrix, labels))
            if score > best_score:
                best_score = score
                best_k = try_k
        k = best_k

    km = KMeans(n_clusters=k, init="k-means++", n_init=10, max_iter=100, random_state=42)
    labels = km.fit_predict(feature_matrix)

    if len(set(labels)) >= 2:
        sample_sil = silhouette_samples(feature_matrix, labels)
        overall_sil = float(silhouette_score(feature_matrix, labels))
    else:
        sample_sil = np.zeros(n)
        overall_sil = 0.0

    domains = []
    for c in range(k):
        mask = labels == c
        count = int(mask.sum())
        if count == 0:
            continue

        member_grades = grades[mask]
        member_spatial = spatial[mask]
        member_sil = sample_sil[mask]

        avg_grade = round(float(member_grades.mean()), 3)
        centroid = member_spatial.mean(axis=0)
        cluster_sil = round(float(member_sil.mean()), 3)

        ell = _pca_ellipsoid(member_spatial)

        domains.append(DomainResult(
            id=f"hybrid-domain-{c + 1}",
            name=DOMAIN_NAMES[c % len(DOMAIN_NAMES)],
            code=f"HD{c + 1}",
            color=DOMAIN_COLORS[c % len(DOMAIN_COLORS)],
            count=count,
            avgGrade=avg_grade,
            gradeRange={
                "min": round(float(member_grades.min()), 3),
                "max": round(float(member_grades.max()), 3),
            },
            silhouetteScore=cluster_sil,
            confidence=round(max(0.0, cluster_sil), 2),
            centroid={
                "x": round(float(centroid[0]), 1),
                "y": round(float(centroid[1]), 1),
                "z": round(float(centroid[2]), 1),
            },
            ellipsoid=ell,
        ))

    domains.sort(key=lambda d: d.count, reverse=True)

    elapsed = int((time.time() - t0) * 1000)
    logger.info(f"Hybrid clustering: {len(domains)} domains, k={k}, alpha={alpha}, {n} composites, {elapsed}ms")

    return HybridClusteringResponse(
        domains=domains,
        overallSilhouette=round(overall_sil, 3),
        compositeCount=n,
        features=geochem_keys,
        alpha=alpha,
        computeTimeMs=elapsed,
    )


@geodriver_router.post("/api/envelope-geometry", response_model=EnvelopeGeometryResponse)
async def envelope_geometry(req: EnvelopeGeometryRequest):
    """Extension d'enveloppe contrainte avec calcul de volumes."""
    t0 = time.time()

    limits = ENVELOPE_LIMITS.get(req.mode, ENVELOPE_LIMITS["strict"])
    spacing = req.spacing

    constrained_domains = []
    total_vol = 0.0
    total_data_vol = 0.0
    total_extrap_vol = 0.0
    max_extension_used = 0.0

    for dom in req.domains:
        pts = np.array([[p.x, p.y, p.z] for p in dom.points])
        n = len(pts)

        if n < 4:
            constrained_domains.append(ConstrainedDomainResult(
                id=dom.id, volume=0, dataVolume=0,
                extrapolatedVolume=0, vertexCount=n, faceCount=0,
            ))
            continue

        try:
            hull_data = ConvexHull(pts)
            data_vol = float(hull_data.volume)
        except Exception:
            data_vol = 0.0

        factor = limits["mineralized"] if dom.isMineralized else limits["unmineralized"]
        extension = spacing * factor
        max_extension_used = max(max_extension_used, extension)

        centroid = pts.mean(axis=0)
        directions = pts - centroid
        norms = np.linalg.norm(directions, axis=1, keepdims=True)
        norms = np.maximum(norms, 1e-10)
        unit_dirs = directions / norms

        if req.confidenceDecay == "exponential":
            decay_weights = np.exp(-norms.flatten() / (spacing * 0.5))
        else:
            decay_weights = np.ones(n)

        extended_pts = pts + unit_dirs * extension * decay_weights.reshape(-1, 1)
        all_pts = np.vstack([pts, extended_pts])

        try:
            hull_total = ConvexHull(all_pts)
            total_domain_vol = float(hull_total.volume)
        except Exception:
            total_domain_vol = data_vol

        extrap_vol = max(0.0, total_domain_vol - data_vol)

        constrained_domains.append(ConstrainedDomainResult(
            id=dom.id,
            volume=round(total_domain_vol, 1),
            dataVolume=round(data_vol, 1),
            extrapolatedVolume=round(extrap_vol, 1),
            vertexCount=len(all_pts),
            faceCount=int(hull_total.simplices.shape[0]) if total_domain_vol > 0 else 0,
        ))

        total_vol += total_domain_vol
        total_data_vol += data_vol
        total_extrap_vol += extrap_vol

    extrap_pct = round((total_extrap_vol / max(total_vol, 1e-10)) * 100, 1)

    elapsed = int((time.time() - t0) * 1000)
    logger.info(f"Envelope geometry: {len(constrained_domains)} domains, mode={req.mode}, "
                f"vol={total_vol:.0f}m³, extrap={extrap_pct}%, {elapsed}ms")

    return EnvelopeGeometryResponse(
        constrainedDomains=constrained_domains,
        volumeReport=VolumeReport(
            totalVolume=round(total_vol, 1),
            dataVolume=round(total_data_vol, 1),
            extrapolatedVolume=round(total_extrap_vol, 1),
            extrapolationPct=extrap_pct,
            maxExtensionUsed=round(max_extension_used, 1),
            spacingUsed=round(spacing, 1),
            mode=req.mode,
        ),
        computeTimeMs=elapsed,
    )
