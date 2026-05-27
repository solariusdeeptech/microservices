# Solarius Microservices

Microservices partagés pour les plateformes Solarius Technology :
- **GEO-ECONOMIX** — Modélisation économique minière
- **GeoMatrix** — (à définir)
- **TerraExploration** — (à définir)

## Architecture

```
┌─────────────┐  ┌─────────────┐  ┌──────────────────┐
│ GEO-ECONOMIX│  │  GeoMatrix  │  │ TerraExploration │
└──────┬──────┘  └──────┬──────┘  └────────┬─────────┘
       │                │                  │
       └────────┬───────┴──────────────────┘
                ▼
     ┌──────────────────────┐
     │  API Gateway (nginx) │  ← Auth par X-API-Key
     └─────┬──────────┬─────┘
           │          │
    ┌──────▼──────┐ ┌─▼──────────────┐
    │   Julia     │ │    Python       │
    │  Geostat.jl │ │  PyVista + MPS  │
    │  Port 8080  │ │  Port 8081      │
    └─────────────┘ └─────────────────┘
```

## Services

### Julia Geostat (port 8080)
- `/health` — Health check
- `/variography` — Calcul de variogrammes expérimentaux + fitting
- `/kriging` — Krigeage ordinaire/simple
- `/sgs` — Simulation gaussienne séquentielle
- `/montecarlo` — Simulation Monte Carlo financière haute performance
- `/pit-optimize` — Optimisation de fosse Lerchs-Grossmann
- `/block-model` — Estimation modèle de blocs

### Python Viz (port 8081)
- `/health` — Health check
- `/render-3d` — Rendu 3D de modèles de blocs (export glTF/image)
- `/mps` — Simulation multi-points (MPS)
- `/sections` — Génération de coupes géologiques
- `/drillholes` — Visualisation 3D de sondages

## Démarrage rapide (local)

```bash
# Lancer tous les services
docker-compose up --build

# Tester
curl http://localhost:8080/health
curl http://localhost:8081/health
```

## Déploiement GCP

Voir [docs/GCP_DEPLOYMENT.md](docs/GCP_DEPLOYMENT.md)

## Authentification multi-tenant

Chaque plateforme s'authentifie via le header `X-API-Key` :
```bash
curl -H "X-API-Key: geoeconomix-prod-xxxx" http://api.solarius-technology.com/julia/kriging
```

Les clés API sont gérées dans le fichier `gateway/api-keys.json` (ou Secret Manager en prod).
