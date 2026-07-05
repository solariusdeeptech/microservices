// ============================================
// FFmpeg post-production: trim capture windows, concat, transcode to
// MP4 (H.264 + yuv420p), optional audio mux, thumbnail extraction.
// ffmpeg binary is installed in the Docker image.
// ============================================
import { spawn } from 'node:child_process';
import { promises as fs } from 'node:fs';
import path from 'node:path';
import { log } from './logger.js';

function run(args, label) {
  return new Promise((resolve, reject) => {
    log.debug(`ffmpeg ${label}`, { args: args.join(' ') });
    const proc = spawn('ffmpeg', args, { stdio: ['ignore', 'ignore', 'pipe'] });
    let stderr = '';
    proc.stderr.on('data', (d) => {
      stderr += d.toString();
      if (stderr.length > 20000) stderr = stderr.slice(-20000);
    });
    proc.on('error', reject);
    proc.on('close', (code) => {
      if (code === 0) resolve();
      else reject(new Error(`ffmpeg ${label} failed (code ${code}): ${stderr.slice(-2000)}`));
    });
  });
}

async function ffprobeDuration(file) {
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

// Common H.264 encode args producing a browser-playable MP4.
function encodeArgs(width, height) {
  // scale to fit + pad to exact dimensions (keeps aspect, no distortion)
  const vf =
    `scale=w=${width}:h=${height}:force_original_aspect_ratio=decrease,` +
    `pad=${width}:${height}:(ow-iw)/2:(oh-ih)/2:color=black,` +
    `format=yuv420p`;
  return [
    '-c:v', 'libx264',
    '-profile:v', 'high',
    '-level', '4.2',
    '-pix_fmt', 'yuv420p',
    '-preset', 'medium',
    '-crf', '20',
    '-movflags', '+faststart',
    '-vf', vf,
  ];
}

/**
 * Produce the final MP4.
 * @param {object} o
 * @param {string} o.inputWebm      source recording
 * @param {Array<{start:number,end:number}>} o.segments  capture windows (sec); empty = whole video
 * @param {number} o.width
 * @param {number} o.height
 * @param {string|null} o.audioPath  optional local audio file
 * @param {string} o.outDir
 * @returns {Promise<{mp4Path:string, thumbPath:string, durationSec:number}>}
 */
export async function produceMp4(o) {
  const { inputWebm, segments, width, height, audioPath, outDir } = o;
  const mp4Path = path.join(outDir, 'output.mp4');
  const thumbPath = path.join(outDir, 'thumbnail.jpg');

  let sourceForEncode = inputWebm;

  // 1. If capture windows are defined, cut & concat them first.
  if (segments && segments.length > 0) {
    const parts = [];
    for (let i = 0; i < segments.length; i++) {
      const seg = segments[i];
      const dur = Math.max(0.1, seg.end - seg.start);
      const partPath = path.join(outDir, `seg_${i}.mp4`);
      await run(
        [
          '-y',
          '-ss', seg.start.toFixed(3),
          '-i', inputWebm,
          '-t', dur.toFixed(3),
          ...encodeArgs(width, height),
          '-an',
          partPath,
        ],
        `cut segment ${i}`
      );
      parts.push(partPath);
    }
    if (parts.length === 1) {
      sourceForEncode = parts[0];
    } else {
      const listFile = path.join(outDir, 'concat.txt');
      await fs.writeFile(
        listFile,
        parts.map((p) => `file '${p}'`).join('\n'),
        'utf-8'
      );
      const concatPath = path.join(outDir, 'concat.mp4');
      await run(
        ['-y', '-f', 'concat', '-safe', '0', '-i', listFile, '-c', 'copy', concatPath],
        'concat segments'
      );
      sourceForEncode = concatPath;
    }
  }

  // 2. Final encode (+ optional audio mux). If we already encoded segments to
  //    mp4 and there is no audio, just move; otherwise (re)encode/mux.
  const alreadyMp4 = sourceForEncode.endsWith('.mp4');
  if (audioPath) {
    const args = ['-y', '-i', sourceForEncode, '-i', audioPath];
    if (alreadyMp4 && segments && segments.length > 0) {
      args.push('-c:v', 'copy');
    } else {
      args.push(...encodeArgs(width, height));
    }
    args.push('-c:a', 'aac', '-b:a', '192k', '-shortest', mp4Path);
    await run(args, 'final encode + audio');
  } else if (alreadyMp4 && segments && segments.length > 0) {
    await fs.rename(sourceForEncode, mp4Path);
  } else {
    await run(
      ['-y', '-i', sourceForEncode, ...encodeArgs(width, height), '-an', mp4Path],
      'final encode'
    );
  }

  // 3. Duration + thumbnail (frame at ~1s or midpoint).
  const durationSec = await ffprobeDuration(mp4Path);
  const at = durationSec > 2 ? Math.min(1.0, durationSec / 2) : 0.1;
  await run(
    ['-y', '-ss', at.toFixed(2), '-i', mp4Path, '-frames:v', '1', '-q:v', '3', thumbPath],
    'thumbnail'
  );

  return { mp4Path, thumbPath, durationSec };
}
