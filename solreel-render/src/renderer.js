// ============================================
// Playwright rendering pipeline.
// Records the whole session to WebM while executing each scene's actions,
// tracking capture_start/capture_end windows (offsets from record start).
// ============================================
import { chromium } from 'playwright';
import { promises as fs } from 'node:fs';
import path from 'node:path';
import { log } from './logger.js';

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function runAction(page, action, ctx) {
  switch (action.type) {
    case 'navigate': {
      const url = action.url || ctx.fallbackUrl;
      await page.goto(url, { waitUntil: 'networkidle', timeout: 60000 }).catch(async () => {
        // networkidle can hang on live apps; fall back to domcontentloaded
        await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 60000 });
      });
      break;
    }
    case 'wait': {
      await sleep(action.durationMs || 1000);
      break;
    }
    case 'scroll': {
      const x = action.x || 0;
      const y = action.y != null ? action.y : 600;
      await page.evaluate(
        ([sx, sy]) => window.scrollBy({ left: sx, top: sy, behavior: 'smooth' }),
        [x, y]
      );
      await sleep(action.durationMs || 600);
      break;
    }
    case 'click': {
      if (action.selector) {
        await page.click(action.selector, { timeout: 15000 }).catch((e) =>
          log.warn('click failed (ignored)', { selector: action.selector, err: String(e) })
        );
      }
      break;
    }
    case 'hover': {
      if (action.selector) {
        await page.hover(action.selector, { timeout: 15000 }).catch((e) =>
          log.warn('hover failed (ignored)', { selector: action.selector, err: String(e) })
        );
      }
      break;
    }
    case 'type': {
      if (action.selector && action.text != null) {
        await page
          .type(action.selector, action.text, { delay: action.delayMs ?? 40 })
          .catch((e) => log.warn('type failed (ignored)', { err: String(e) }));
      }
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
