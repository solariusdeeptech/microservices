// ============================================
// MediaForge Process — Express server
// Contract (Model A, polling):
//   POST /process  { jobId, operation, inputUrls, ffmpegCommand, ... } -> { requestId }
//   POST /status   { requestId } -> { status: PROCESSING|SUCCESS|FAILED, outputFiles?, error? }
//   GET  /health   -> 200 (no auth)
// Auth: Authorization: Bearer <MEDIAFORGE_API_KEY>
// ============================================
import express from 'express';
import { v4 as uuidv4 } from 'uuid';
import { config, assertConfig } from './config.js';
import { log } from './logger.js';
import { requireBearer } from './auth.js';
import { validateProcess, validateStatus } from './validation.js';
import { writeJob, readJob, writeJobIndex, readJobIndex } from './gcs.js';
import { processJob } from './processor.js';

assertConfig();

const app = express();
app.use(express.json({ limit: '2mb' }));

// --- Health (no auth) ---
app.get('/health', (_req, res) => {
  res.json({ status: 'ok', service: 'mediaforge-process', ts: new Date().toISOString() });
});

// --- Submit a processing job ---
app.post('/process', requireBearer, async (req, res) => {
  const check = validateProcess(req.body);
  if (!check.ok) {
    return res.status(400).json({
      error: 'INVALID_REQUEST',
      message: 'Request validation failed.',
      details: check.issues,
    });
  }
  const request = check.data;

  // --- Idempotence on jobId ---
  // If we already have a job for this jobId that is not FAILED, return it.
  try {
    const idx = await readJobIndex(request.jobId);
    if (idx && idx.requestId) {
      const existing = await readJob(idx.requestId);
      if (existing && existing.status !== 'FAILED') {
        log.info('Idempotent hit — returning existing requestId', {
          jobId: request.jobId,
          requestId: idx.requestId,
          status: existing.status,
        });
        return res.status(202).json({ requestId: idx.requestId });
      }
    }
  } catch (err) {
    log.warn('Idempotency check failed (continuing to create new job)', { err: String(err) });
  }

  const requestId = uuidv4();
  const now = new Date().toISOString();
  const job = {
    requestId,
    jobId: request.jobId,
    operation: request.operation,
    status: 'QUEUED',
    outputFiles: null,
    error: null,
    durationMs: null,
    createdAt: now,
    updatedAt: now,
  };

  try {
    await writeJob(job);
    await writeJobIndex(request.jobId, requestId);
  } catch (err) {
    log.error('Failed to persist job', { jobId: request.jobId, err: String(err) });
    return res.status(500).json({ error: 'STORE_ERROR', message: 'Could not persist job.' });
  }

  // Fire-and-forget. Cloud Run keeps CPU allocated (--no-cpu-throttling).
  processJob(job, request).catch((err) =>
    log.error('Unhandled job error', { requestId, err: String(err) })
  );

  return res.status(202).json({ requestId });
});

// --- Poll job status ---
app.post('/status', requireBearer, async (req, res) => {
  const check = validateStatus(req.body);
  if (!check.ok) {
    return res.status(400).json({ error: 'INVALID_REQUEST', details: check.issues });
  }
  try {
    const job = await readJob(check.data.requestId);
    if (!job) return res.status(404).json({ error: 'NOT_FOUND', message: 'Unknown requestId.' });

    if (job.status === 'SUCCESS') {
      return res.json({ status: 'SUCCESS', outputFiles: job.outputFiles || {} });
    }
    if (job.status === 'FAILED') {
      return res.json({ status: 'FAILED', error: job.error || 'Unknown error' });
    }
    // QUEUED or PROCESSING both surface as PROCESSING to MediaForge.
    return res.json({ status: 'PROCESSING' });
  } catch (err) {
    log.error('Failed to read job', { err: String(err) });
    return res.status(500).json({ error: 'STORE_ERROR', message: 'Could not read job.' });
  }
});

app.use((_req, res) => res.status(404).json({ error: 'NOT_FOUND' }));

app.listen(config.port, () => {
  log.info('mediaforge-process listening', { port: config.port, bucket: config.gcs.bucket });
});
