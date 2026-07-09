// Minimal structured logger (stdout JSON — Cloud Run friendly).
// SECURITY: callers must NEVER pass signed URLs, credentials or callback
// secrets into the `extra` payload. Log identifiers (jobId, requestId,
// operation, status, durationMs) only.
function emit(severity, msg, extra) {
  const entry = { severity, message: msg, ...(extra || {}), ts: new Date().toISOString() };
  const line = JSON.stringify(entry);
  if (severity === 'ERROR') console.error(line);
  else console.log(line);
}

export const log = {
  info: (msg, extra) => emit('INFO', msg, extra),
  warn: (msg, extra) => emit('WARNING', msg, extra),
  error: (msg, extra) => emit('ERROR', msg, extra),
  debug: (msg, extra) => emit('DEBUG', msg, extra),
};
