FROM julia:1.10-bookworm AS builder  
WORKDIR /app  
  
RUN julia -e 'using Pkg; Pkg.activate("."); Pkg.add(["HTTP", "JSON3", "GeoStats", "Meshes", "GeoTables", "Distributions"]); Pkg.precompile()'  
  
COPY src/ src/  
RUN julia --project=. -e 'include("src/warmup.jl")' || true  
  
FROM julia:1.10-bookworm  
WORKDIR /app  
RUN apt-get update && apt-get install -y curl && rm -rf /var/lib/apt/lists/*  
COPY --from=builder /root/.julia /root/.julia  
COPY --from=builder /app .  
ENV JULIA_PORT=8080  
ENV JULIA_NUM_THREADS=auto  
ENV JULIA_DEPOT_PATH=/root/.julia  
HEALTHCHECK --interval=30s --timeout=10s --retries=3 CMD curl -f http://localhost:8080/health || exit 1  
EXPOSE 8080  
CMD ["julia", "--project=.", "--threads=auto", "src/server.jl"]