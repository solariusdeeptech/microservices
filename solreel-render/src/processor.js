// ============================================
// Background job processor: orchestrates render → post-prod → upload,
// persisting status transitions to GCS at every step.
// Statuses: QUEUED → RENDERING → PROCESSING → COMPLETED | FAILED
// ============================================
import { promises as fs } from 'node:fs';
import path from 'node:path';
import { config } from './config.js';
import { log } from './logger.js';
import { writeJob } from './gcs.js';
import { uploadPublic } from './gcs.js';
import { renderScenario } from './renderer.js';
import { produceMp4 } from './ffmpeg.js';
import { downloadToFile } from './download.js';

async function setStatus(job, patch) {
  Object.assign(job, patch, { updatedAt: new Date().toISOString() });
  await writeJob(job);
  log.info('Job status', { jobId: job.jobId, status: job.status });
}

export async function processJob(job, scenario) {
  const outDir = path.join(config.workDir, job.jobId);
  await fs.mkdir(outDir, { recursive: true });

  try {
    // 1. RENDER
    await setStatus(job, { status: 'RENDERING' });
    const { webmPath, segments } = await renderScenario(scenario, outDir);

    // 2. PROCESS (FFmpeg)
    await setStatus(job, { status: 'PROCESSING' });
    let audioPath = null;
    if (scenario.hasAudio && scenario.audioUrl) {
      audioPath = await downloadToFile(scenario.audioUrl, outDir, 'audio-track');
    }
    const { mp4Path, thumbPath, durationSec } = await produceMp4({
      inputWebm: webmPath,
      segments,
      width: scenario.dimensions.width,
      height: scenario.dimensions.height,
      audioPath,
      outDir,
    });

    // 3. UPLOAD (public)
    const base = `renders/${scenario.projectId}/${job.jobId}`;
    const video = await uploadPublic(mp4Path, `${base}/video.mp4`, 'video/mp4');
    const thumb = await uploadPublic(thumbPath, `${base}/thumbnail.jpg`, 'image/jpeg');
    const stat = await fs.stat(mp4Path);

    // 4. COMPLETE
    await setStatus(job, {
      status: 'COMPLETED',
      videoUrl: video.url,
      thumbnailUrl: thumb.url,
      duration: Math.round(durationSec * 100) / 100,
      fileSize: stat.size,
      error: null,
    });
  } catch (err) {
    log.error('Job failed', { jobId: job.jobId, err: String(err && err.stack || err) });
    await setStatus(job, {
      status: 'FAILED',
      error: String(err && err.message ? err.message : err),
    });
  } finally {
    // Best-effort cleanup of transient artifacts.
    fs.rm(outDir, { recursive: true, force: true }).catch(() => {});
  }
}
