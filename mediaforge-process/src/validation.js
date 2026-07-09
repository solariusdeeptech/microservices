// Zod validation for the MediaForge ↔ microservice contract.
import { z } from 'zod';
import { config } from './config.js';

// POST /process
export const processSchema = z.object({
  jobId: z.string().min(1),
  operation: z.string().min(1),
  inputUrls: z.array(z.string().url()).min(1).max(config.limits.maxInputs),
  outputName: z.string().optional(),
  outputFormat: z.string().optional(),
  params: z.record(z.any()).optional().default({}),
  // MediaForge builds the full ffmpeg command with {{in_N}} / {{out_N}}
  // placeholders. We execute it verbatim after substituting local paths.
  ffmpegCommand: z.string().min(1),
  // Optional Model B (callback) target.
  callbackUrl: z.string().url().optional(),
});

// POST /status
export const statusSchema = z.object({
  requestId: z.string().min(1),
});

function validate(schema, body) {
  const result = schema.safeParse(body);
  if (!result.success) {
    const issues = result.error.issues.map(
      (i) => `${i.path.join('.') || '(root)'}: ${i.message}`
    );
    return { ok: false, issues };
  }
  return { ok: true, data: result.data };
}

export const validateProcess = (body) => validate(processSchema, body);
export const validateStatus = (body) => validate(statusSchema, body);
