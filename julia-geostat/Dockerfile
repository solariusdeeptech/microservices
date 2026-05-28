FROM julia:1.10-bookworm AS builder
WORKDIR /app
COPY Project.toml .
COPY install_packages.jl .
COPY precompile_app.jl .
COPY build_sysimage.jl .
COPY src/ src/
RUN julia install_packages.jl
RUN julia --project=. -e 'using Pkg; Pkg.add("PackageCompiler")'
RUN julia --project=. build_sysimage.jl
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
