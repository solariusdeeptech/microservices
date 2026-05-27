# ============================================
# Solarius Python Viz Microservice
# PyVista + MPS avec rendu CPU (OSMesa)
# ============================================

# Stage 1: Build
FROM python:3.11-bookworm AS builder

# Install system dependencies for VTK/OSMesa (CPU rendering)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1-mesa-dev \
    libosmesa6-dev \
    libglu1-mesa-dev \
    xvfb \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application
COPY app/ app/

# Stage 2: Runtime
FROM python:3.11-slim-bookworm

# Install runtime dependencies for OSMesa
RUN apt-get update && apt-get install -y --no-install-recommends \
    libosmesa6 \
    libgl1-mesa-glx \
    libglu1-mesa \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy from builder
COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin
COPY --from=builder /app .

# Environment
ENV PYTHON_PORT=8081
ENV PYVISTA_OFF_SCREEN=true
ENV DISPLAY=:99
ENV MESA_GL_VERSION_OVERRIDE=4.5

# Health check
HEALTHCHECK --interval=30s --timeout=10s --retries=3 \
  CMD curl -f http://localhost:8081/health || exit 1

EXPOSE 8081

# Start with Xvfb for offscreen rendering fallback
CMD ["sh", "-c", "Xvfb :99 -screen 0 1024x768x24 &>/dev/null & uvicorn app.main:app --host 0.0.0.0 --port ${PYTHON_PORT} --workers 2"]
