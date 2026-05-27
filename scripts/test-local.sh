#!/bin/bash
# ============================================
# Test local des microservices
# Lancer après: docker-compose up --build
# ============================================

set -e

API_KEY="dev-geoeconomix-key"
BASE_JULIA="http://localhost:8080"
BASE_PYTHON="http://localhost:8081"
GATEWAY="http://localhost:80"

echo "\n=== Test Health Checks ==="
echo "\n--- Julia Health ---"
curl -s $BASE_JULIA/health | python3 -m json.tool

echo "\n--- Python Health ---"
curl -s $BASE_PYTHON/health | python3 -m json.tool

echo "\n--- Gateway Health ---"
curl -s $GATEWAY/health | python3 -m json.tool

echo "\n=== Test Variography ==="
curl -s -X POST $BASE_JULIA/variography \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $API_KEY" \
  -d '{
    "data_x": [0, 10, 20, 30, 40, 50, 60, 70, 80, 90, 100],
    "data_y": [0, 10, 20, 30, 40, 50, 60, 70, 80, 90, 100],
    "data_z": [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0],
    "data_values": [1.2, 2.3, 1.8, 3.1, 2.7, 1.5, 2.9, 3.4, 2.1, 1.7, 2.5],
    "num_lags": 10,
    "fit_model": "spherical"
  }' | python3 -m json.tool

echo "\n=== Test Kriging ==="
curl -s -X POST $BASE_JULIA/kriging \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $API_KEY" \
  -d '{
    "data_x": [0, 50, 100],
    "data_y": [0, 50, 100],
    "data_z": [0, 0, 0],
    "data_values": [1.0, 3.0, 2.0],
    "grid_x": [25, 50, 75],
    "grid_y": [25, 50, 75],
    "grid_z": [0],
    "variogram": { "model": "spherical", "nugget": 0.1, "sill": 1.5, "range": 80 },
    "method": "ordinary",
    "max_neighbors": 8
  }' | python3 -m json.tool

echo "\n=== Test MPS ==="
curl -s -X POST $BASE_PYTHON/mps \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $API_KEY" \
  -d '{
    "training_image": [[1,1,0,0,1],[1,0,0,1,1],[0,0,1,1,0],[0,1,1,0,0],[1,1,0,0,1]],
    "grid_size": { "nx": 10, "ny": 10, "nz": 1 },
    "template_size": { "x": 3, "y": 3 },
    "num_realizations": 3,
    "seed": 42
  }' | python3 -m json.tool

echo "\n=== Test via Gateway ==="
curl -s $GATEWAY/julia/health | python3 -m json.tool
curl -s $GATEWAY/python/health | python3 -m json.tool

echo "\n✅ Tous les tests passent !"
