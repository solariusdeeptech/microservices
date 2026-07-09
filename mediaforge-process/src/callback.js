// ============================================
// Model B — signed webhook callback to MediaForge with retry + backoff.
// The callback MUST eventually arrive; polling (Model A) is the safety net.
// ============================================
import crypto from 'node:crypto';
import { config } from './config.js';
import { log } from './logger.js';

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// HMAC-SHA256 of the raw body, hex, prefixed "sha256=" (X-Signature header).
export function signBody(rawBody, secret) {
  return 'sha256=' + crypto.createHmac('sha256', secret).update(rawBody).digest('hex');
}

// Exponential backoff with jitter, capped at 30s.
function backoffMs(attempt) {
  const base = Math.min(30000, 1000 * 2 ** (attempt - 1));
  return base + Math.floor(Math.random() * 500);
}

/**
 * Deliver a callback. Fire-and-forget from the processor; resolves once the
 * webhook succeeds or all retries are exhausted.
 * @param {string} url
 * @param {{jobId:string,status:string,outputFiles:object|null,errorMessage:string|null,durationMs:number|null}} payload
 */
export async function sendCallback(url, payload) {
  if (!url) return;
  if (!config.callback.secret) {
    log.warn('callback skipped: CALLBACK_SECRET not configured', { jobId: payload.jobId });
    return;
  }
  const rawBody = JSON.stringify(payload);
  const signature = signBody(rawBody, config.callback.secret);
  const max = config.callback.maxRetries;

  for (let attempt = 1; attempt <= max; attempt++) {
    try {
      const res = await fetch(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-Signature': signature },
        body: rawBody,
      });
      if (res.ok) {
        log.info('callback delivered', { jobId: payload.jobId, status: payload.status, attempt });
        return;
      }
      throw new Error(`HTTP ${res.status}`);
    } catch (err) {
      // Never log the callback URL (kept out of logs by contract).
      log.warn('callback attempt failed', { jobId: payload.jobId, attempt, err: String(err) });
      if (attempt < max) await sleep(backoffMs(attempt));
    }
  }
  log.error('callback permanently failed (polling remains available)', { jobId: payload.jobId });
}
