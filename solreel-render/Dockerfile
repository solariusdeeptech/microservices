# ============================================
# SolReel Render — Playwright (Chromium) + FFmpeg + Node
# Base image ships Chromium and all system deps matching Playwright 1.49.1.
# ============================================
FROM mcr.microsoft.com/playwright:v1.49.1-jammy

WORKDIR /app

# ffmpeg + ffprobe (post-production) and curl (health checks)
RUN apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg curl && \
    rm -rf /var/lib/apt/lists/*

# Install production deps (browsers already present in the base image)
COPY package.json ./
RUN npm install --omit=dev --no-audit --no-fund

COPY src/ src/

ENV NODE_ENV=production
ENV PORT=8080
ENV WORK_DIR=/tmp/solreel

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD curl -f http://localhost:8080/health || exit 1

CMD ["node", "src/server.js"]
