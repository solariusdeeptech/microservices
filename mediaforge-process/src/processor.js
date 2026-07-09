// ============================================
// Background job processor: download inputs -> run ffmpeg -> upload outputs
// -> persist status -> fire callback. Cleans /tmp after every job.
// Statuses: QUEUED -> PROCESSING -> SUCCESS | FAILED
// ============================================
import { promises as fs } from 'node:fs';
import path from 'node:path';
import { config } from './config.js';
import { log } from './logger.js';
import { downloadToFile } from './download.js';
import { uploadFile, writeJob } from './gcs.js';
import { execute, findPlaceholders, contentTypeFor } from './ffmpeg.js';
import { sendCallback } from './callback.js';

function extFromUrl(url) {
  try {
    const p = new URL(url).pathname;
    const ext = p.split('.').pop();
    return ext && ext.length <= 5 ? `.${ext}` : '.bin';
  } catch {
    return '.bin';
  }
}

async function setStatus(job, patch) {
  Object.assign(job, patch, { updatedAt: new Date().toISOString() });
  await writeJob(job);
  log.info('Job status', { jobId: job.jobId, requestId: job.requestId, status: job.status });
}

export async function processJob(job, request) {
  const startedAt = Date.now();
  const outDir = path.join(config.workDir, job.requestId);
  await fs.mkdir(outDir, { recursive: true });

  try {
    await setStatus(job, { status: 'PROCESSING' });

    // 1. DOWNLOAD inputs referenced by {{in_N}} (stream to disk).
    const inIdx = findPlaceholders(request.ffmpegCommand, 'in');
    const inputPaths = {};
    for (const n of inIdx) {
      const url = request.inputUrls[n - 1];
      if (!url) throw new Error(`Missing inputUrls[${n - 1}] for placeholder in_${n}`);
      const dest = path.join(outDir, `in_${n}${extFromUrl(url)}`);
      await downloadToFile(url, dest);
      inputPaths[n] = dest;
    }
    log.info('Inputs downloaded', { jobId: job.jobId, count: inIdx.length, operation: request.operation });

    // 2. RUN ffmpeg (no artificial 600s limit; bounded by ffmpegTimeoutMs).
    const produced = await execute({
      operation: request.operation,
      ffmpegCommand: request.ffmpegCommand,
      inputPaths,
      outDir,
      outputName: request.outputName,
      outputFormat: request.outputFormat,
      params: request.params,
    });

    // 3. UPLOAD outputs to GCS -> outputFiles map (public HTTPS URLs).
    const outputFiles = {};
    for (const out of produced) {
      const filename = path.basename(out.path);
      const destName = `${config.gcs.outputPrefix}${job.jobId}/${filename}`;
      outputFiles[out.key] = await uploadFile(out.path, destName, contentTypeFor(out.path));
    }

    const durationMs = Date.now() - startedAt;
    await setStatus(job, { status: 'SUCCESS', outputFiles, error: null, durationMs });
    await sendCallback(request.callbackUrl, {
      jobId: job.jobId,
      status: 'SUCCESS',
      outputFiles,
      errorMessage: null,
      durationMs,
    });
  } catch (err) {
    const durationMs = Date.now() - startedAt;
    const message = String(err && err.message ? err.message : err);
    log.error('Job failed', { jobId: job.jobId, requestId: job.requestId, err: message });
    await setStatus(job, { status: 'FAILED', error: message, durationMs });
    await sendCallback(request.callbackUrl, {
      jobId: job.jobId,
      status: 'FAILED',
      outputFiles: null,
      errorMessage: message,
      durationMs,
    });
  } finally {
    fs.rm(outDir, { recursive: true, force: true }).catch(() => {});
  }
}
