// ============================================
// SolReel Render — Configuration
// ============================================

export const config = {
  port: parseInt(process.env.PORT || '8080', 10),

  // Cloud / storage
  gcpProjectId: process.env.GCP_PROJECT_ID || 'microservices-497617',
  gcpRegion: process.env.GCP_REGION || 'europe-west1',
  // Dedicated output bucket for SolReel renders (public-read).
  gcsBucket: process.env.GCS_BUCKET_SOLREEL || process.env.GCS_BUCKET || 'solreel-renders',

  // Auth — Bearer token(s). Comma-separated list of accepted keys.
  // SOLREEL_API_KEY (primary) + optional SOLREEL_API_KEYS (extra, comma separated).
  apiKeys: [
    process.env.SOLREEL_API_KEY,
    ...(process.env.SOLREEL_API_KEYS || '').split(','),
  ]
    .map((k) => (k || '').trim())
    .filter(Boolean),

  // Hard limits enforced by the contract
  limits: {
    maxDurationSec: 300,
    maxScenes: 8,
    maxWidth: 1920,
    maxHeight: 1920,
  },

  // Rendering defaults
  defaultFps: 30,
  // Working directory for transient artifacts (webm, mp4, thumbnails)
  workDir: process.env.WORK_DIR || '/tmp/solreel',
};

export function assertConfig() {
  if (config.apiKeys.length === 0) {
    // Do not crash — but log a loud warning. In prod SOLREEL_API_KEY must be set.
    console.warn(
      '[config] WARNING: no SOLREEL_API_KEY configured — all authenticated requests will be rejected.'
    );
  }
}
