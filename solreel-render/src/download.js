// Download a remote asset (e.g. audio track) to a local file.
import { promises as fs } from 'node:fs';
import path from 'node:path';

export async function downloadToFile(url, outDir, filename) {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`Failed to download ${url}: HTTP ${res.status}`);
  const buf = Buffer.from(await res.arrayBuffer());
  const dest = path.join(outDir, filename);
  await fs.writeFile(dest, buf);
  return dest;
}
