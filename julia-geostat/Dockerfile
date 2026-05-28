FROM julia:1.10-bookworm AS builder
WORKDIR /app
COPY Project.toml .
COPY src/ src/
COPY precompile_app.jl .
RUN julia -e 'using Pkg; Pkg.activate("."); Pkg.add(["HTTP", "JSON3", "GeoStats", "Meshes", "GeoTables", "Distributions"]); Pkg.precompile()'
RUN julia --project=. -e 'using Pkg; Pkg.add("PackageCompiler")'
RUN julia --project=. -e 'using PackageCompiler; create_sysimage([:HTTP, :JSON3, :GeoStats, :Meshes, :GeoTables, :Distributions]; sysimage_path="/app/sysimage.so", precompile_execution_file="/app/precompile_app.jl")'
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
