# ============================================
# Solarius Julia Geostat Microservice
# Multi-stage build pour image optimisée
# ============================================

# Stage 1: Build — Install packages and precompile
FROM julia:1.10-bookworm AS builder

WORKDIR /app

# Copy project files
COPY Project.toml .

# Install Julia packages (long step, cached by Docker)
RUN julia --project=. -e 'using Pkg; Pkg.instantiate(); Pkg.precompile()'

# Copy source code
COPY src/ src/

# Precompile application
RUN julia --project=. -e 'include("src/warmup.jl")' || true

# Stage 2: Runtime
FROM julia:1.10-bookworm

WORKDIR /app

# Copy compiled packages and source
COPY --from=builder /root/.julia /root/.julia
COPY --from=builder /app .

# Environment
ENV JULIA_PORT=8080
ENV JULIA_NUM_THREADS=auto
ENV JULIA_DEPOT_PATH=/root/.julia

# Health check
HEALTHCHECK --interval=30s --timeout=10s --retries=3 \
  CMD curl -f http://localhost:8080/health || exit 1

EXPOSE 8080

# Start server
CMD ["julia", "--project=.", "--threads=auto", "src/server.jl"]
