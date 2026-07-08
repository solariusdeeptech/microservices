// ============================================
// Credential redaction helpers.
//
// `type` actions carry plaintext secrets (passwords, tokens) in their
// `value`/`text` field. These MUST NEVER reach logs, stdout/stderr, or any
// persisted artifact (GCS job status, crash dumps, etc.).
//
// This module centralizes masking so every code path that logs or persists a
// scenario goes through the same sanitizer.
// ============================================

export const REDACTED = '[REDACTED]';

// Action types whose value is sensitive and must be masked before it can be
// logged or persisted.
const SENSITIVE_ACTIONS = new Set(['type']);

/**
 * Mask the sensitive fields of a single action IN PLACE.
 * Used for in-memory cleanup right after a `type` action executes, so the
 * plaintext credential no longer lives in the scenario object.
 */
export function redactActionInPlace(action) {
  if (action && SENSITIVE_ACTIONS.has(action.type)) {
    if (action.value !== undefined) action.value = REDACTED;
    if (action.text !== undefined) action.text = REDACTED;
  }
  return action;
}

/**
 * Deep-clone a scenario and mask every sensitive value.
 * Use this ANY time a scenario (or part of it) might be logged or persisted.
 * The original object is left untouched.
 */
export function redactScenario(scenario) {
  if (scenario == null || typeof scenario !== 'object') return scenario;
  let clone;
  try {
    clone = structuredClone(scenario);
  } catch {
    clone = JSON.parse(JSON.stringify(scenario));
  }
  for (const scene of clone.scenes || []) {
    for (const action of scene.actions || []) {
      redactActionInPlace(action);
    }
  }
  return clone;
}
