FROM python:3.11-slim-bookworm

WORKDIR /app

# Install build dependencies for gstools/pykrige/pymaxflow (Cython C++ extensions)
RUN apt-get update && \
    apt-get install -y --no-install-recommends gcc g++ && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Remove build deps to keep image small (compiled .so files persist)
RUN apt-get purge -y gcc g++ && apt-get autoremove -y

COPY src/ src/

EXPOSE 8080

# 4 workers for 4 vCPU, timeout 120s keep-alive, 600s graceful shutdown
CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "4", "--timeout-keep-alive", "120", "--timeout-graceful-shutdown", "600"]
