FROM julia:1.10-bookworm AS builder
WORKDIR /app

# Install packages by name
RUN julia -e 'using Pkg; Pkg.activate("."); Pkg.add(["HTTP", "JSON3", "GeoStats", "Meshes", "GeoTables", "Distributions"]); Pkg.precompile()'

COPY src/ src/

# Warmup at build time only (errors ignored)
RUN julia --project=. -e 'try; include("src/warmup.jl"); catch e; println("Warmup skipped: ", e); end'

FROM julia:1.10-bookworm
WORKDIR /app
RUN apt-get update && apt-get install -y curl && rm -rf /var/lib/apt/lists/*
COPY --from=builder /root/.julia /root/.julia
COPY --from=builder /app .
ENV JULIA_NUM_THREADS=auto
ENV JULIA_DEPOT_PATH=/root/.julia
EXPOSE 8080
CMD ["julia", "--project=.", "--threads=auto", "src/server.jl"]
