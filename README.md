# Solarius Microservices

Backend microservices for **Solarius DeepTech** platforms:
- **GEO-ECONOMIX** — Mining economics & geostatistics
- **GeoMatrix** — Advanced geological modeling
- **TerraExploration** — Exploration data platform

## Architecture

```
GEO-ECONOMIX ──┐
GeoMatrix ──────┤── API Gateway (Cloud Run) ──┬── Jobs légers → Cloud Run
TerraExploration┘   (auth, routing, metrics)  │   (python-geostat, python-viz)
                                               │
                                               └── Jobs lourds → Cloud Batch
                                                   (Julia GeoStats.jl, Python heavy)
                                                        │
                                                        └── GCS (résultats)
```

## Services

| Service | Port | Description | Cloud Run |
|---------|------|-------------|----------|
| **api-gateway** | 8080 | Routing, auth, metrics, Cloud Batch dispatch | 1 CPU / 1 GiB / min:1 / max:5 |
| **python-geostat** | 8080 | Variography, kriging, SGS, Monte Carlo, ML domaining | 4 CPU / 8 GiB / min:1 / max:3 |
| **python-viz** | 8080 | PyVista 3D, cross-sections, MPS | 2 CPU / 4 GiB / min:0 / max:2 |
| **julia-geostat** | 8080 | GeoStats.jl heavy computation (Cloud Batch only) | Cloud Batch containers |
| **solreel-render** | 8080 | SolReel animated-video rendering (Playwright + FFmpeg → MP4 on GCS) | 4 CPU / 8 GiB / min:1 / max:3 / concurrency:1 |

## API Gateway Endpoints

### Sync (Cloud Run proxy)
```
POST /api/variography        → python-geostat
POST /api/kriging            → python-geostat
POST /api/sgs                → python-geostat
POST /api/montecarlo         → python-geostat
POST /api/pit-optimize       → python-geostat
POST /api/block-model        → python-geostat
POST /api/ml-domaining       → python-geostat
POST /api/deep-kriging       → python-geostat
POST /api/spatial-continuity → python-geostat
POST /api/hybrid-clustering  → python-geostat
POST /api/envelope-geometry  → python-geostat
POST /api/render-3d          → python-viz
POST /api/sections           → python-viz
POST /api/drillholes         → python-viz
POST /api/mps                → python-viz
```

### Async (Cloud Batch)
```
POST /api/v1/jobs/submit                  # Submit heavy job
GET  /api/v1/jobs/{job_id}/status         # Poll status
GET  /api/v1/jobs/{job_id}/result         # Download result from GCS
```

### Smart Routing
- **< 50K points / 100K blocks / 50 realizations** → Cloud Run (sync)
- **≥ thresholds** → Cloud Batch (async)
- Force: `?mode=async` or `?mode=sync`

### Metrics
```
GET /api/v1/metrics                       # All platforms
GET /api/v1/metrics?platform=geoeconomix  # Single platform
```

## Authentication

All requests require `X-API-Key` header.

## Local Development

```bash
docker-compose up --build
curl http://localhost:8080/health
```

## Deployment

CI/CD: GitHub Actions → Cloud Build → Artifact Registry → Cloud Run.
