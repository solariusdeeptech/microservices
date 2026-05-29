FROM julia:1.10-bookworm

WORKDIR /app

# Install python3 (health shim) + curl + socat (port forward)
RUN apt-get update && \
    apt-get install -y --no-install-recommends python3 curl socat && \
    rm -rf /var/lib/apt/lists/*

ENV JULIA_NUM_THREADS=4
ENV JULIA_DEPOT_PATH=/root/.julia

# Install Julia packages WITHOUT full precompile (fast build ~2 min)
# Only precompile HTTP+JSON3 (small, fast) for quicker server start
ENV JULIA_PKG_PRECOMPILE_AUTO=0
RUN julia --threads=4 -e '\
  using Pkg; \
  Pkg.add(["HTTP", "JSON3", "GeoStats", "Meshes", "GeoTables", \
           "Distributions", "Variography", "GeoStatsSolvers"]); \
  println("All packages installed")'

# Precompile ONLY HTTP+JSON3 (takes ~30s, needed for fast server start)
RUN julia --threads=4 -e '\
  using Pkg; \
  Pkg.precompile("HTTP"); \
  Pkg.precompile("JSON3"); \
  println("HTTP+JSON3 precompiled")'

COPY health_shim.py /app/health_shim.py
COPY startup.sh /app/startup.sh
RUN chmod +x /app/startup.sh

COPY src/ src/

EXPOSE 8080

CMD ["/app/startup.sh"]
