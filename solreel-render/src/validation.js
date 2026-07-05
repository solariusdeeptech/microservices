// Zod validation of the SolReel render scenario (contract-frozen).
import { z } from 'zod';
import { config } from './config.js';

const actionSchema = z.object({
  type: z.enum([
    'navigate',
    'wait',
    'scroll',
    'click',
    'hover',
    'type',
    'capture_start',
    'capture_end',
  ]),
  // Optional fields depending on action type
  url: z.string().url().optional(),
  selector: z.string().optional(),
  text: z.string().optional(),
  // scroll target / amount
  x: z.number().optional(),
  y: z.number().optional(),
  // generic timing (ms)
  durationMs: z.number().int().positive().max(120000).optional(),
  // typing delay per char (ms)
  delayMs: z.number().int().nonnegative().max(2000).optional(),
});

const sceneSchema = z.object({
  sceneId: z.string(),
  sceneIndex: z.number().int().nonnegative(),
  title: z.string().optional().default(''),
  url: z.string().url().optional(),
  narration: z.string().optional().default(''),
  durationSec: z.number().positive().max(config.limits.maxDurationSec),
  actions: z.array(actionSchema).default([]),
});

export const scenarioSchema = z
  .object({
    projectId: z.string(),
    targetUrl: z.string().url(),
    dimensions: z.object({
      width: z.number().int().positive().max(config.limits.maxWidth),
      height: z.number().int().positive().max(config.limits.maxHeight),
    }),
    format: z.string().optional().default('mp4'),
    totalDurationSec: z.number().positive().max(config.limits.maxDurationSec),
    hasAudio: z.boolean().optional().default(false),
    audioUrl: z.string().url().optional(),
    scenes: z
      .array(sceneSchema)
      .min(1)
      .max(config.limits.maxScenes),
  })
  .refine((s) => !(s.hasAudio && !s.audioUrl), {
    message: 'audioUrl is required when hasAudio is true',
    path: ['audioUrl'],
  });

export function validateScenario(body) {
  const result = scenarioSchema.safeParse(body);
  if (!result.success) {
    const issues = result.error.issues.map(
      (i) => `${i.path.join('.') || '(root)'}: ${i.message}`
    );
    return { ok: false, issues };
  }
  return { ok: true, data: result.data };
}
