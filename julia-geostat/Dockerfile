FROM julia:1.10-bookworm
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/*
ENV JULIA_PKG_PRECOMPILE_AUTO=0
ENV JULIA_NUM_THREADS=auto
ENV JULIA_DEPOT_PATH=/root/.julia
RUN julia -e 'using Pkg; Pkg.add(["HTTP", "JSON3", "GeoStats", "Meshes", "GeoTables", "Distributions"])'
COPY Project.toml .
COPY src/ src/
EXPOSE 8080
CMD ["julia", "--project=.", "--threads=auto", "src/server.jl"]
