// ============================================
// Playwright rendering pipeline.
// Records the whole session to WebM while executing each scene's actions,
// tracking capture_start/capture_end windows (offsets from record start).
// ============================================
import { chromium } from 'playwright';
import { promises as fs } from 'node:fs';
import path from 'node:path';
import { log } from './logger.js';
import { redactActionInPlace } from './redact.js';

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// Resolve the selector for an action, preferring the canonical `target`,
// falling back to the legacy `selector` field.
const selectorOf = (action) => action.target || action.selector || null;

// Split a possibly comma-separated multi-selector into individual selectors.
const splitSelectors = (sel) =>
  String(sel)
    .split(',')
    .map((s) => s.trim())
    .filter(Boolean);

/**
 * Fill a form field robustly.
 * Uses locator().first().fill() (works with React/Vue controlled inputs, unlike
 * key-by-key type()). If the composite multi-selector fails, retries each
 * individual selector in turn. NEVER logs the value being typed.
 */
async function robustFill(page, sel, value) {
  const candidates = [sel, ...splitSelectors(sel)];
  let lastErr;
  for (const candidate of candidates) {
    try {
      await page.locator(candidate).first().fill(String(value), { timeout: 8000 });
      return true;
    } catch (e) {
      lastErr = e;
    }
  }
  // Log the failure WITHOUT the value (credential safety): only type + target.
  log.warn('type failed (ignored, continuing)', { type: 'type', target: sel, err: String(lastErr) });
  return false;
}

async function runAction(page, action, ctx) {
  switch (action.type) {
    case 'navigate': {
      const url = action.target || action.url || ctx.fallbackUrl;
      await page.goto(url, { waitUntil: 'networkidle', timeout: 60000 }).catch(async () => {
        // networkidle can hang on live apps; fall back to domcontentloaded
        await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 60000 });
      });
      break;
    }
    case 'wait': {
      // Honor requested duration (login redirects need a real pause).
      await sleep(action.durationMs || 1000);
      break;
    }
    case 'scroll': {
      // `value` (px) is the canonical vertical offset; fall back to legacy x/y.
      const numericValue = typeof action.value === 'number' ? action.value : Number(action.value);
      const hasValue = action.value != null && !Number.isNaN(numericValue);
      const x = action.x || 0;
      const y = hasValue ? numericValue : action.y != null ? action.y : 600;
      await page.evaluate(
        ([sx, sy]) => window.scrollBy({ left: sx, top: sy, behavior: 'smooth' }),
        [x, y]
      );
      await sleep(action.durationMs || 600);
      break;
    }
    case 'click': {
      const sel = selectorOf(action);
      if (sel) {
        try {
          await page.locator(sel).first().click({ timeout: 15000 });
          // A login click frequently triggers a redirect / SPA route change.
          await page.waitForLoadState('networkidle', { timeout: 10000 }).catch(() => {});
        } catch (e) {
          // Non-standard form / missing button: log & CONTINUE the scenario.
          log.warn('click failed (ignored, continuing)', { target: sel, err: String(e) });
        }
      }
      break;
    }
    case 'hover': {
      const sel = selectorOf(action);
      if (sel) {
        await page.locator(sel).first().hover({ timeout: 15000 }).catch((e) =>
          log.warn('hover failed (ignored)', { target: sel, err: String(e) })
        );
      }
      break;
    }
    case 'type': {
      const sel = selectorOf(action);
      const value = action.value != null ? action.value : action.text;
      if (sel && value != null) {
        await robustFill(page, sel, value);
      }
      // CREDENTIAL CLEANUP: wipe the plaintext from the in-memory scenario the
      // instant it has been used, so it cannot leak via a crash dump.
      redactActionInPlace(action);
      break;
    }
    case 'capture_start': {
      ctx.currentStart = (Date.now() - ctx.recordStart) / 1000;
      break;
    }
    case 'capture_end': {
      if (ctx.currentStart != null) {
        const end = (Date.now() - ctx.recordStart) / 1000;
        ctx.segments.push({ start: ctx.currentStart, end });
        ctx.currentStart = null;
      }
      break;
    }
    default:
      break;
  }
}

/**
 * Render a scenario to a WebM file.
 * @returns {Promise<{webmPath:string, segments:Array<{start:number,end:number}>}>}
 */
export async function renderScenario(scenario, outDir) {
  const { dimensions, scenes, targetUrl } = scenario;
  const videoDir = path.join(outDir, 'video');
  await fs.mkdir(videoDir, { recursive: true });

  const browser = await chromium.launch({
    headless: true,
    args: [
      '--no-sandbox',
      '--disable-setuid-sandbox',
      '--disable-dev-shm-usage',
      '--disable-gpu',
      '--force-color-profile=srgb',
      '--hide-scrollbars',
    ],
  });

  const ctx = {
    recordStart: 0,
    segments: [],
    currentStart: null,
    fallbackUrl: targetUrl,
  };

  try {
    const context = await browser.newContext({
      viewport: { width: dimensions.width, height: dimensions.height },
      deviceScaleFactor: 1,
      recordVideo: {
        dir: videoDir,
        size: { width: dimensions.width, height: dimensions.height },
      },
    });
    const page = await context.newPage();
    ctx.recordStart = Date.now();

    // Initial navigation to the target if the first scene has no navigate action.
    await page.goto(targetUrl, { waitUntil: 'domcontentloaded', timeout: 60000 }).catch(() => {});

    const ordered = [...scenes].sort((a, b) => a.sceneIndex - b.sceneIndex);
    for (const scene of ordered) {
      log.info('Rendering scene', { sceneId: scene.sceneId, index: scene.sceneIndex });
      if (scene.url) {
        await page
          .goto(scene.url, { waitUntil: 'networkidle', timeout: 60000 })
          .catch(() => page.goto(scene.url, { waitUntil: 'domcontentloaded', timeout: 60000 }).catch(() => {}));
      }
      const sceneStart = Date.now();
      for (const action of scene.actions || []) {
        await runAction(page, action, ctx);
      }
      // Hold the scene for the remaining of its declared duration.
      const elapsed = (Date.now() - sceneStart) / 1000;
      const remaining = scene.durationSec - elapsed;
      if (remaining > 0) await sleep(remaining * 1000);
    }

    // Close a dangling capture window.
    if (ctx.currentStart != null) {
      ctx.segments.push({ start: ctx.currentStart, end: (Date.now() - ctx.recordStart) / 1000 });
      ctx.currentStart = null;
    }

    const video = page.video();
    await context.close(); // finalizes the webm
    const webmPath = await video.path();
    log.info('Recording finalized', { webmPath, segments: ctx.segments.length });
    return { webmPath, segments: ctx.segments };
  } finally {
    await browser.close().catch(() => {});
  }
}
