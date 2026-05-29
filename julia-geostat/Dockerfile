FROM julia:1.10-bookworm

WORKDIR /app

# Install curl for health checks
RUN apt-get update && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/*

# IMPORTANT: Use same thread/env settings as runtime to keep precomp cache valid
ENV JULIA_NUM_THREADS=4
ENV JULIA_DEPOT_PATH=/root/.julia

# Install ALL packages WITH full precompilation
RUN julia --threads=4 -e '\
  using Pkg; \
  Pkg.add(["HTTP", "JSON3", "GeoStats", "Meshes", "GeoTables", \
           "Distributions", "Variography", "GeoStatsSolvers"]); \
  println("Packages installed, starting full precompilation..."); \
  Pkg.precompile(); \
  println("Full precompilation complete!")'

# Warm up: load HTTP+JSON3 to verify they load fast (these are needed at startup)
RUN julia --threads=4 -e '\
  t0 = time(); \
  using HTTP, JSON3, Logging, Dates; \
  elapsed = round(time()-t0, digits=1); \
  println("HTTP+JSON3+stdlib loaded in $(elapsed)s"); \
  if elapsed > 60; \
    println("WARNING: HTTP+JSON3 load took >60s, precomp cache may be invalid"); \
  end'

# Also warm up GeoStats packages to verify precompilation works
RUN julia --threads=4 -e '\
  t0 = time(); \
  using GeoStats, Meshes, GeoTables, Distributions; \
  elapsed = round(time()-t0, digits=1); \
  println("GeoStats packages loaded in $(elapsed)s"); \
  println("All packages verified OK")'

COPY src/ src/

EXPOSE 8080

# Use same thread count as build to keep precomp cache valid
CMD ["julia", "--threads=4", "src/server.jl"]
