FROM python:3.11-bookworm AS builder  
RUN apt-get update && apt-get install -y --no-install-recommends libgl1-mesa-dev libosmesa6-dev libglu1-mesa-dev && rm -rf /var/lib/apt/lists/*  
WORKDIR /app  
COPY requirements.txt .  
RUN pip install --no-cache-dir -r requirements.txt  
COPY app/ app/  
  
FROM python:3.11-slim-bookworm  
RUN apt-get update && apt-get install -y --no-install-recommends libosmesa6 libgl1-mesa-glx libglu1-mesa xvfb curl && rm -rf /var/lib/apt/lists/*  
WORKDIR /app  
COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages  
COPY --from=builder /usr/local/bin /usr/local/bin  
COPY --from=builder /app .  
ENV PYTHON_PORT=8081  
ENV PYVISTA_OFF_SCREEN=true  
ENV DISPLAY=:99  
ENV MESA_GL_VERSION_OVERRIDE=4.5  
HEALTHCHECK --interval=30s --timeout=10s --retries=3 CMD curl -f http://localhost:8081/health || exit 1  
EXPOSE 8081  
CMD ["sh", "-c", "Xvfb :99 -screen 0 1024x768x24 > /dev/null 2>&1 & sleep 2 && uvicorn app.main:app --host 0.0.0.0 --port 8081 --workers 2"]