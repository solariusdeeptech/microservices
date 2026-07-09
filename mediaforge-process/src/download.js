// ============================================
// Input download: stream a remote PUBLIC HTTP(S) URL to a local file.
// The microservice never talks to S3/GCS for inputs — MediaForge provides
// a public URL for each input file. Streaming avoids buffering multi-GB
// files in memory. The URL is NEVER logged (it may be sensitive).
// ============================================
import { createWriteStream } from 'node:fs';
import { Readable } from 'node:stream';
import { pipeline } from 'node:stream/promises';

export async function downloadToFile(url, dest) {
  const res = await fetch(url);
  if (!res.ok || !res.body) {
    throw new Error(`Download failed: HTTP ${res.status}`);
  }
  await pipeline(Readable.fromWeb(res.body), createWriteStream(dest));
  return dest;
}
