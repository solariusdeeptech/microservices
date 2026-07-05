// ============================================
// SolReel Render — Express server
// Contract:
//   POST /render        → 202 { jobId, status: "QUEUED" }
//   GET  /render/:jobId → 200 { jobId, status, videoUrl, duration, fileSize, thumbnailUrl, error }
//   GET  /health        → 200 (no auth)
// Auth: Authorization: Bearer <key>
// ============================================
import express from 'express';
import { v4 as uuidv4 } from 'uuid';
import { config, assertConfig } from './config.js';
import { log } from './logger.js';
import { requireBearer } from './auth.js';
import { validateScenario } from './validation.js';
import { writeJob, readJob } from './gcs.js';
import { processJob } from './processor.js';

assertConfig();

const app = express();
app.use(express.json({ limit: '2mb' }));

// --- Health (no auth) ---
app.get('/health', (_req, res) => {
  res.json({ status: 'ok', service: 'solreel-render', ts: new Date().toISOString() });
});

// --- Submit a render job ---
app.post('/render', requireBearer, async (req, res) => {
  const check = validateScenario(req.body);
  if (!check.ok) {
    return res.status(400).json({
      error: 'INVALID_SCENARIO',
      message: 'Scenario validation failed.',
      details: check.issues,
    });
  }
  const scenario = check.data;
  const jobId = uuidv4();
  const now = new Date().toISOString();
  const job = {
    jobId,
    projectId: scenario.projectId,
    status: 'QUEUED',
    videoUrl: null,
    thumbnailUrl: null,
    duration: null,
    fileSize: null,
    error: null,
    createdAt: now,
    updatedAt: now,
  };

  try {
    await writeJob(job);
  } catch (err) {
    log.error('Failed to persist job', { jobId, err: String(err) });
    return res.status(500).json({ error: 'STORE_ERROR', message: 'Could not persist job.' });
  }

  // Fire-and-forget background processing. Cloud Run keeps CPU allocated
  // (--no-cpu-throttling) so this survives after the 202 response.
  processJob(job, scenario).catch((err) =>
    log.error('Unhandled job error', { jobId, err: String(err) })
  );

  return res.status(202).json({ jobId, status: 'QUEUED' });
});

// --- Poll job status ---
app.get('/render/:jobId', requireBearer, async (req, res) => {
  const { jobId } = req.params;
  try {
    const job = await readJob(jobId);
    if (!job) {
      return res.status(404).json({ error: 'NOT_FOUND', message: 'Job not found.' });
    }
    return res.json({
      jobId: job.jobId,
      status: job.status,
      videoUrl: job.videoUrl,
      duration: job.duration,
      fileSize: job.fileSize,
      thumbnailUrl: job.thumbnailUrl,
      error: job.error,
    });
  } catch (err) {
    log.error('Failed to read job', { jobId, err: String(err) });
    return res.status(500).json({ error: 'STORE_ERROR', message: 'Could not read job.' });
  }
});

app.use((_req, res) => res.status(404).json({ error: 'NOT_FOUND' }));

app.listen(config.port, () => {
  log.info('solreel-render listening', { port: config.port, bucket: config.gcsBucket });
});
