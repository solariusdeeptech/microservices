# =============================================================================
# Julia Geostat Microservice — Dockerfile avec PackageCompiler
# Solarius Technology
#
# SOLUTION au cold start : PackageCompiler crée un sysimage binaire
# précompilé pendant le build Docker. Au runtime, Julia charge le
# sysimage en 3-5 secondes au lieu de 15 minutes de JIT.
# =============================================================================

# ---- Stage 1 : Build + PackageCompiler sysimage ----
FROM julia:1.10-bookworm AS builder

WORKDIR /app

# Copier les fichiers projet
COPY Project.toml .

# Installer les packages + PackageCompiler
RUN julia --project=. -e '
    using Pkg;
    Pkg.instantiate();
    Pkg.add("PackageCompiler");
    Pkg.precompile();
    @info "All packages installed and precompiled"
'

# Copier le code source et le workload de précompilation
COPY src/ src/
COPY precompile_app.jl .

# Créer le sysimage avec PackageCompiler (15-30 min au build, 3s au runtime)
RUN julia --project=. -e '
    using PackageCompiler;
    packages = [
        :HTTP, :JSON3, :GeoStats, :Meshes, :GeoTables,
        :Distributions, :Random, :Statistics, :LinearAlgebra, :Logging, :Dates
    ];
    create_sysimage(
        packages;
        sysimage_path="/app/sysimage.so",
        precompile_execution_file="/app/precompile_app.jl",
        cpu_target="generic;sandybridge,-xsaveopt,clone_all;haswell,-rdrnd,base(1)"
    );
    @info "Sysimage created successfully!"
'

# ---- Stage 2 : Runtime léger ----
FROM julia:1.10-bookworm

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/*

# Copier le sysimage précompilé
COPY --from=builder /app/sysimage.so /app/sysimage.so

# Copier les packages Julia
COPY --from=builder /root/.julia /root/.julia

# Copier le code source
COPY --from=builder /app/Project.toml /app/Project.toml
COPY --from=builder /app/src/ /app/src/

ENV JULIA_NUM_THREADS=auto
ENV JULIA_DEPOT_PATH=/root/.julia

EXPOSE 8080

# Démarrer avec le sysimage précompilé (3-5s au lieu de 15min)
CMD ["julia", "--sysimage=/app/sysimage.so", "--project=.", "--threads=auto", "src/server.jl"]
