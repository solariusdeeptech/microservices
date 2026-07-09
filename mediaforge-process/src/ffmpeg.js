// ============================================
// FFmpeg execution: verbatim run of the MediaForge-built command after
// substituting {{in_N}} / {{out_N}} placeholders with local file paths.
// Supports single & multiple outputs; EXTRACT_FRAMES is handled specially
// (frames -> zip).
// ============================================
import { spawn } from 'node:child_process';
import { createWriteStream, promises as fs } from 'node:fs';
import path from 'node:path';
import archiver from 'archiver';
import { config } from './config.js';
import { log } from './logger.js';

// ---- Shell-like tokenizer (respects single & double quotes) ----
// Keeps a quoted filter_complex value as ONE argv element and strips the
// surrounding quotes, matching the convention MediaForge already uses.
export function tokenize(cmd) {
  const tokens = [];
  let cur = '';
  let has = false;
  let inS = false;
  let inD = false;
  for (let i = 0; i < cmd.length; i++) {
    const c = cmd[i];
    if (inS) {
      if (c === "'") inS = false;
      else cur += c;
      has = true;
    } else if (inD) {
      if (c === '"') inD = false;
      else cur += c;
      has = true;
    } else if (c === "'") {
      inS = true;
      has = true;
    } else if (c === '"') {
      inD = true;
      has = true;
    } else if (/\s/.test(c)) {
      if (has) {
        tokens.push(cur);
        cur = '';
        has = false;
      }
    } else {
      cur += c;
      has = true;
    }
  }
  if (has) tokens.push(cur);
  return tokens;
}

// Placeholder regex tolerant to {{in_1}} and {{{{in_1}}}} (double or quad braces).
const placeholderRe = (kind) => new RegExp(`\\{\\{+\\s*${kind}_(\\d+)\\s*\\}\\}+`, 'g');

export function findPlaceholders(cmd, kind) {
  const re = placeholderRe(kind);
  const set = new Set();
  let m;
  while ((m = re.exec(cmd)) !== null) set.add(Number(m[1]));
  return [...set].sort((a, b) => a - b);
}

function substituteToken(token, inputPaths, outputPaths) {
  return token
    .replace(placeholderRe('in'), (m, n) => {
      const p = inputPaths[Number(n)];
      if (!p) throw new Error(`Unresolved input placeholder in_${n}`);
      return p;
    })
    .replace(placeholderRe('out'), (m, n) => {
      const p = outputPaths[Number(n)];
      if (!p) throw new Error(`Unresolved output placeholder out_${n}`);
      return p;
    });
}

// Build the final argv from the raw command + resolved paths.
export function buildArgs(ffmpegCommand, inputPaths, outputPaths) {
  let tokens = tokenize(ffmpegCommand);
  // Drop an accidental leading "ffmpeg".
  if (tokens.length && tokens[0].toLowerCase() === 'ffmpeg') tokens = tokens.slice(1);
  const args = tokens.map((t) => substituteToken(t, inputPaths, outputPaths));
  // Force overwrite (never block on an interactive prompt).
  if (args[0] !== '-y') args.unshift('-y');
  return args;
}

function run(args, timeoutMs) {
  return new Promise((resolve, reject) => {
    // Log the argv WITHOUT input URLs (args hold local paths only — safe).
    log.debug('ffmpeg exec', { argc: args.length });
    const proc = spawn('ffmpeg', args, { stdio: ['ignore', 'ignore', 'pipe'] });
    let stderr = '';
    let timedOut = false;
    const timer = setTimeout(() => {
      timedOut = true;
      proc.kill('SIGKILL');
    }, timeoutMs);
    proc.stderr.on('data', (d) => {
      stderr += d.toString();
      if (stderr.length > 20000) stderr = stderr.slice(-20000);
    });
    proc.on('error', (e) => {
      clearTimeout(timer);
      reject(e);
    });
    proc.on('close', (code) => {
      clearTimeout(timer);
      if (timedOut) return reject(new Error(`ffmpeg timed out after ${timeoutMs}ms`));
      if (code === 0) return resolve();
      reject(new Error(`ffmpeg failed (code ${code}): ${stderr.slice(-2000)}`));
    });
  });
}

export async function ffprobeDuration(file) {
  return new Promise((resolve) => {
    const proc = spawn('ffprobe', [
      '-v', 'error',
      '-show_entries', 'format=duration',
      '-of', 'default=noprint_wrappers=1:nokey=1',
      file,
    ]);
    let out = '';
    proc.stdout.on('data', (d) => (out += d.toString()));
    proc.on('close', () => resolve(parseFloat(out.trim()) || 0));
    proc.on('error', () => resolve(0));
  });
}

function zipDir(dir, zipPath) {
  return new Promise((resolve, reject) => {
    const output = createWriteStream(zipPath);
    const archive = archiver('zip', { zlib: { level: 9 } });
    output.on('close', resolve);
    archive.on('error', reject);
    archive.pipe(output);
    archive.directory(dir, false);
    archive.finalize();
  });
}

/**
 * Execute the scenario.
 * @returns {Promise<Array<{key:string, path:string}>>} produced outputs, key = 'out_N'
 */
export async function execute({ operation, ffmpegCommand, inputPaths, outDir, outputName, outputFormat, params }) {
  const outIdx = findPlaceholders(ffmpegCommand, 'out');
  if (outIdx.length === 0) throw new Error('ffmpegCommand has no {{out_N}} placeholder');

  // ---- Special case: EXTRACT_FRAMES => frames dir -> single zip ----
  if (operation === 'EXTRACT_FRAMES') {
    const framesDir = path.join(outDir, 'frames');
    await fs.mkdir(framesDir, { recursive: true });
    const imgExt = (outputFormat || params?.targetFormat || 'jpg').replace(/^\./, '');
    const pattern = path.join(framesDir, `frame_%04d.${imgExt}`);
    const outputPaths = {};
    for (const n of outIdx) outputPaths[n] = pattern;
    await run(buildArgs(ffmpegCommand, inputPaths, outputPaths), config.limits.ffmpegTimeoutMs);
    const zipPath = path.join(outDir, (outputName || 'frames').replace(/\.[^.]+$/, '') + '.zip');
    await zipDir(framesDir, zipPath);
    return [{ key: 'out_1', path: zipPath }];
  }

  // ---- Standard case: resolve each out_N to a concrete file ----
  const outputPaths = {};
  const produced = [];
  for (const n of outIdx) {
    const ext = resolveExt({ n, outputName, outputFormat, params, inputPaths });
    const base = n === 1 && outputName ? outputName : `output_${n}.${ext}`;
    const filePath = path.join(outDir, base);
    outputPaths[n] = filePath;
    produced.push({ key: `out_${n}`, path: filePath });
  }
  await run(buildArgs(ffmpegCommand, inputPaths, outputPaths), config.limits.ffmpegTimeoutMs);
  return produced;
}

function resolveExt({ n, outputName, outputFormat, params }) {
  if (n === 1 && outputName && /\.[^.]+$/.test(outputName)) {
    return outputName.split('.').pop();
  }
  if (outputFormat) return outputFormat.replace(/^\./, '');
  if (params?.targetFormat) return String(params.targetFormat).replace(/^\./, '');
  return 'mp4';
}

export function contentTypeFor(filePath) {
  const ext = (filePath.split('.').pop() || '').toLowerCase();
  const map = {
    mp4: 'video/mp4', webm: 'video/webm', mov: 'video/quicktime', mkv: 'video/x-matroska',
    avi: 'video/x-msvideo', gif: 'image/gif', jpg: 'image/jpeg', jpeg: 'image/jpeg',
    png: 'image/png', webp: 'image/webp', mp3: 'audio/mpeg', wav: 'audio/wav',
    aac: 'audio/aac', m4a: 'audio/mp4', ogg: 'audio/ogg', flac: 'audio/flac', zip: 'application/zip',
  };
  return map[ext] || 'application/octet-stream';
}
