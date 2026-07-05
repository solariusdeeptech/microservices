// ============================================
// GCS helpers: persistent job store + public asset upload.
// Auth uses the Cloud Run runtime service account (Application Default
// Credentials / Workload Identity) — NO key file.
// ============================================
import { Storage } from '@google-cloud/storage';
import { config } from './config.js';
import { log } from './logger.js';

const storage = new Storage({ projectId: config.gcpProjectId });
const bucket = storage.bucket(config.gcsBucket);

const statusPath = (jobId) => `jobs/${jobId}/status.json`;

// ---- Persistent job store (survives instance changes) ----
export async function writeJob(job) {
  const file = bucket.file(statusPath(job.jobId));
  await file.save(JSON.stringify(job), {
    contentType: 'application/json',
    resumable: false,
    metadata: { cacheControl: 'no-store' },
  });
}

export async function readJob(jobId) {
  const file = bucket.file(statusPath(jobId));
  try {
    const [buf] = await file.download();
    return JSON.parse(buf.toString('utf-8'));
  } catch (err) {
    if (err && (err.code === 404 || err.code === 'ENOENT')) return null;
    throw err;
  }
}

// ---- Public asset upload (video / thumbnail) ----
// Returns a public HTTPS URL. Bucket must allow public read
// (uniform bucket-level access + allUsers:objectViewer).
export async function uploadPublic(localPath, destName, contentType) {
  const dest = bucket.file(destName);
  await bucket.upload(localPath, {
    destination: destName,
    resumable: false,
    metadata: {
      contentType,
      cacheControl: 'public, max-age=31536000, immutable',
    },
  });
  const encoded = destName
    .split('/')
    .map(encodeURIComponent)
    .join('/');
  const url = `https://storage.googleapis.com/${config.gcsBucket}/${encoded}`;
  log.info('Uploaded asset to GCS', { destName, url });
  return { url, gcsPath: `gs://${config.gcsBucket}/${destName}` };
}
