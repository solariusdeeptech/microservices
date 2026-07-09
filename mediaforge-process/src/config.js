// ============================================
// MediaForge Process — Configuration (v2, GCS)
// Inputs are read via public HTTP(S) URLs (NO AWS).
// Outputs + job state are written to Google Cloud Storage.
// Auth to GCS uses the Cloud Run runtime service account
// (Application Default Credentials / Workload Identity) — no key file.
// ============================================

function normalizePrefix(p, fallback) {
  let s = (p || fallback || '').trim();
  if (!s) return '';
  s = s.replace(/^\/+/, ''); // no leading slash
  if (!s.endsWith('/')) s += '/';
  return s;
}

export const config = {
  port: parseInt(process.env.PORT || '8080', 10),

  gcs: {
    projectId: process.env.GCP_PROJECT_ID || 'microservices-497617',
    // Dedicated output bucket for MediaForge (public-read + 24h lifecycle).
    bucket:
      process.env.GCS_BUCKET_MEDIAFORGE ||
      process.env.GCS_BUCKET ||
      'mediaforge-process-outputs',
    // Prefix for output files. Lifecycle rule (24h) applies to this + jobs/.
    outputPrefix: normalizePrefix(process.env.GCS_OUTPUT_PREFIX, 'outputs/'),
    // Prefix for the persistent job/idempotency store.
    jobPrefix: 'jobs/',
    // Serve outputs via a public HTTPS URL (decision locked in spec v2 §3.4).
    publicOutput: (process.env.PUBLIC_OUTPUT || 'true').toLowerCase() !== 'false',
  },

  // Inbound auth — Bearer key(s). GCP_PROCESSING_API_KEY is the primary name
  // (per spec v2). MEDIAFORGE_API_KEY / API_KEY accepted as aliases;
  // GCP_PROCESSING_API_KEYS (CSV) supports rotation.
  apiKeys: [
    process.env.GCP_PROCESSING_API_KEY,
    process.env.MEDIAFORGE_API_KEY,
    process.env.API_KEY,
    ...(process.env.GCP_PROCESSING_API_KEYS || '').split(','),
  ]
    .map((k) => (k || '').trim())
    .filter(Boolean),

  // Outbound callback (Model B) signing + retry policy.
  callback: {
    secret: process.env.CALLBACK_SECRET || '',
    maxRetries: parseInt(process.env.CALLBACK_MAX_RETRIES || '6', 10),
  },

  limits: {
    maxInputs: parseInt(process.env.MAX_INPUTS || '10', 10),
    // Hard cap on a single ffmpeg run. Kept below the Cloud Run request
    // timeout (3600s) so we can fail gracefully and still fire the callback.
    ffmpegTimeoutMs: parseInt(process.env.FFMPEG_TIMEOUT_MS || '3300000', 10),
  },

  // Working directory for transient input/output files.
  workDir: process.env.WORK_DIR || '/tmp/mediaforge',
};

export function assertConfig() {
  if (config.apiKeys.length === 0) {
    console.warn(
      '[config] WARNING: no GCP_PROCESSING_API_KEY configured — all authenticated requests will be rejected.'
    );
  }
  if (!config.gcs.bucket) {
    console.warn(
      '[config] WARNING: GCS_BUCKET_MEDIAFORGE not set — output upload will fail until the bucket is configured.'
    );
  }
  if (!config.callback.secret) {
    console.warn(
      '[config] NOTE: CALLBACK_SECRET not set — webhook callbacks (Model B) will be skipped; polling (Model A) still works.'
    );
  }
}
