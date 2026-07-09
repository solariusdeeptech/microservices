// ============================================
// GCS helpers: public output upload + persistent job/idempotency store.
// Auth uses the Cloud Run runtime service account (Application Default
// Credentials / Workload Identity) — NO key file, NO AWS.
// Retention (24h) is handled by a GCS bucket lifecycle rule, not code.
// ============================================
import { Storage } from '@google-cloud/storage';
import { config } from './config.js';
import { log } from './logger.js';

const storage = new Storage({ projectId: config.gcs.projectId });
const bucket = storage.bucket(config.gcs.bucket);

const encode = (name) => name.split('/').map(encodeURIComponent).join('/');

export function publicUrl(destName) {
  return `https://storage.googleapis.com/${config.gcs.bucket}/${encode(destName)}`;
}

// ---- Upload a local output file to GCS; return a public HTTPS URL ----
export async function uploadFile(localPath, destName, contentType) {
  await bucket.upload(localPath, {
    destination: destName,
    resumable: false,
    metadata: {
      contentType,
      // Objects are short-lived (lifecycle deletes after 24h).
      cacheControl: 'public, max-age=3600',
    },
  });
  if (config.gcs.publicOutput) {
    // Best-effort: if the bucket already grants public read at the prefix
    // (uniform bucket-level access), makePublic() may be a no-op or fail;
    // do not treat that as fatal.
    try {
      await bucket.file(destName).makePublic();
    } catch (err) {
      log.warn('makePublic skipped (bucket may use uniform public access)', {
        err: String(err && err.message ? err.message : err),
      });
    }
  }
  const url = publicUrl(destName);
  log.info('Uploaded output to GCS', { destName });
  return url;
}

// ---- Persistent job store (survives instance scale events) ----
const jobPath = (requestId) => `${config.gcs.jobPrefix}${requestId}.json`;
const indexPath = (jobId) => `${config.gcs.jobPrefix}by-jobid/${jobId}.json`;

async function putJson(name, obj) {
  await bucket.file(name).save(JSON.stringify(obj), {
    contentType: 'application/json',
    resumable: false,
    metadata: { cacheControl: 'no-store' },
  });
}

async function getJson(name) {
  try {
    const [buf] = await bucket.file(name).download();
    return JSON.parse(buf.toString('utf-8'));
  } catch (err) {
    if (err && (err.code === 404 || err.code === 'ENOENT')) return null;
    throw err;
  }
}

export const writeJob = (job) => putJson(jobPath(job.requestId), job);
export const readJob = (requestId) => getJson(jobPath(requestId));
export const writeJobIndex = (jobId, requestId) =>
  putJson(indexPath(jobId), { jobId, requestId });
export const readJobIndex = (jobId) => getJson(indexPath(jobId));
