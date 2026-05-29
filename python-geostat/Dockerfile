FROM python:3.11-slim-bookworm

WORKDIR /app

# Install build dependencies for gstools/pykrige
RUN apt-get update && \
    apt-get install -y --no-install-recommends gcc g++ && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Remove build deps to keep image small
RUN apt-get purge -y gcc g++ && apt-get autoremove -y

COPY src/ src/

EXPOSE 8080

CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "2", "--timeout-keep-alive", "120"]
