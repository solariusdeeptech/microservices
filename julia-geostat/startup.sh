#!/bin/bash
set -e

PORT=${PORT:-8080}
JULIA_PORT=8081

echo "[startup] ============================================"
echo "[startup] Solarius Julia-Geostat Startup"
echo "[startup] PORT=$PORT, JULIA_PORT=$JULIA_PORT"
echo "[startup] ============================================"

# ── Phase 1: Instant health responder (Python) ──────────────
echo "[startup] Phase 1: Starting Python health shim on port $PORT"
python3 /app/health_shim.py &
SHIM_PID=$!

# Verify shim is up
sleep 1
if ! kill -0 $SHIM_PID 2>/dev/null; then
  echo "[startup] ERROR: Health shim failed to start!"
  exit 1
fi
echo "[startup] Health shim running (PID=$SHIM_PID)"

# ── Phase 2: Start Julia on internal port ────────────────────
echo "[startup] Phase 2: Starting Julia server on port $JULIA_PORT"
export JULIA_INTERNAL_PORT=$JULIA_PORT
julia --threads=4 -e "
  ENV[\"PORT\"] = \"$JULIA_PORT\"
  include(\"src/server.jl\")
" &
JULIA_PID=$!
echo "[startup] Julia starting (PID=$JULIA_PID)"

# ── Phase 3: Wait for Julia to be ready ──────────────────────
echo "[startup] Phase 3: Waiting for Julia HTTP server..."
MAX_WAIT=1200  # 20 minutes max
ELAPSED=0
while [ $ELAPSED -lt $MAX_WAIT ]; do
  # Check Julia process is still alive
  if ! kill -0 $JULIA_PID 2>/dev/null; then
    echo "[startup] ERROR: Julia process died!"
    # Keep shim running so Cloud Run doesn't kill the container
    wait $SHIM_PID
    exit 1
  fi
  
  # Try to reach Julia health endpoint
  HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" --connect-timeout 2 --max-time 5 \
    http://localhost:$JULIA_PORT/health 2>/dev/null || echo "000")
  
  if [ "$HTTP_CODE" = "200" ]; then
    echo "[startup] Julia is READY after ${ELAPSED}s!"
    break
  fi
  
  # Log progress every 30 seconds
  if [ $((ELAPSED % 30)) -eq 0 ] && [ $ELAPSED -gt 0 ]; then
    echo "[startup] Still waiting for Julia... (${ELAPSED}s elapsed, HTTP=$HTTP_CODE)"
  fi
  
  sleep 5
  ELAPSED=$((ELAPSED + 5))
done

if [ $ELAPSED -ge $MAX_WAIT ]; then
  echo "[startup] ERROR: Julia failed to start within ${MAX_WAIT}s"
  # Keep shim alive so Cloud Run keeps the container
  wait $SHIM_PID
  exit 1
fi

# ── Phase 4: Swap traffic from shim to Julia ─────────────────
echo "[startup] Phase 4: Swapping traffic to Julia"

# Kill the Python shim
kill $SHIM_PID 2>/dev/null
wait $SHIM_PID 2>/dev/null || true
echo "[startup] Health shim stopped"

# Brief pause for port release
sleep 1

# Forward PORT -> JULIA_PORT using socat
socat TCP-LISTEN:$PORT,reuseaddr,fork TCP:localhost:$JULIA_PORT &
FWD_PID=$!
echo "[startup] Traffic forwarding active (PID=$FWD_PID)"

# Verify forwarding works
sleep 1
VERIFY=$(curl -s --connect-timeout 2 --max-time 5 http://localhost:$PORT/health 2>/dev/null || echo "FAIL")
if echo "$VERIFY" | grep -q '"status"'; then
  echo "[startup] ============================================"
  echo "[startup] SERVICE FULLY OPERATIONAL"
  echo "[startup] Julia PID=$JULIA_PID, Forwarder PID=$FWD_PID"
  echo "[startup] ============================================"
else
  echo "[startup] WARNING: Forwarding verification failed, but continuing..."
fi

# Wait for Julia (main process)
wait $JULIA_PID
RET=$?
echo "[startup] Julia exited with code $RET"
kill $FWD_PID 2>/dev/null
exit $RET
