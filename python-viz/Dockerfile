FROM python:3.11-bookworm AS builder
RUN apt-get update && apt-get install -y --no-install-recommends libgl1-mesa-dev libosmesa6-dev libglu1-mesa-dev && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app/ app/
FROM python:3.11-slim-bookworm
RUN apt-get update && apt-get install -y --no-install-recommends libosmesa6 libgl1 libglu1-mesa libgomp1 xvfb curl && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin
COPY --from=builder /app .
ENV PYVISTA_OFF_SCREEN=true
ENV PYVISTA_USE_PANEL=false
ENV VTK_DEFAULT_RENDER_WINDOW_OFFSCREEN=1
ENV LIBGL_ALWAYS_SOFTWARE=1
ENV MESA_GL_VERSION_OVERRIDE=3.3
ENV DISPLAY=:99
ENV MPLBACKEND=Agg
EXPOSE 8080
CMD ["sh", "-c", "Xvfb :99 -screen 0 1024x768x24 -ac > /dev/null 2>&1 & sleep 1 && exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8080} --workers 2"]
