# ============================================
# MediaForge Process — Node 20 + native FFmpeg
# Heavy transcode / compress / GIF jobs offloaded from MediaForge.
# ============================================
FROM node:20-bookworm-slim

WORKDIR /app

# ffmpeg + ffprobe (native binaries) and curl (health checks)
RUN apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg curl ca-certificates && \
    rm -rf /var/lib/apt/lists/*

# Install production deps
COPY package.json ./
RUN npm install --omit=dev --no-audit --no-fund

COPY src/ src/

ENV NODE_ENV=production
ENV PORT=8080
ENV WORK_DIR=/tmp/mediaforge

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD curl -f http://localhost:8080/health || exit 1

CMD ["node", "src/server.js"]
