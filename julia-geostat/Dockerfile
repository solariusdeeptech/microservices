# =============================================================================
# Julia Geostat Microservice — Dockerfile avec PackageCompiler
# Solarius Technology
# =============================================================================

# ---- Stage 1 : Build + PackageCompiler sysimage ----
FROM julia:1.10-bookworm AS builder

WORKDIR /app

# Copier les fichiers projet
COPY Project.toml .

# Installer les packages + PackageCompiler
RUN julia --project=. -e 'using Pkg; Pkg.instantiate(); Pkg.add("PackageCompiler"); Pkg.precompile(); @info "All packages installed"'

# Copier le code source et le workload de précompilation
COPY src/ src/
COPY precompile_app.jl .

# Créer le sysimage avec PackageCompiler
RUN julia --project=. -e 'using PackageCompiler; create_sysimage([:HTTP, :JSON3, :GeoStats, :Meshes, :GeoTables, :Distributions, :Random, :Statistics, :LinearAlgebra, :Logging, :Dates]; sysimage_path="/app/sysimage.so", precompile_execution_file="/app/precompile_app.jl", cpu_target="generic;sandybridge,-xsaveopt,clone_all;haswell,-rdrnd,base(1)"); @info "Sysimage created!"'

# ---- Stage 2 : Runtime léger ----
FROM julia:1.10-bookworm

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/*

COPY --from=builder /app/sysimage.so /app/sysimage.so
COPY --from=builder /root/.julia /root/.julia
COPY --from=builder /app/Project.toml /app/Project.toml
COPY --from=builder /app/src/ /app/src/

ENV JULIA_NUM_THREADS=auto
ENV JULIA_DEPOT_PATH=/root/.julia

EXPOSE 8080

CMD ["julia", "--sysimage=/app/sysimage.so", "--project=.", "--threads=auto", "src/server.jl"]
