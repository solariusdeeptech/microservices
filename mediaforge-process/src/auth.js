// Bearer token authentication middleware.
// Contract: MediaForge sends `Authorization: Bearer <MEDIAFORGE_API_KEY>`.
import { config } from './config.js';

export function requireBearer(req, res, next) {
  const header = req.headers['authorization'] || '';
  const match = header.match(/^Bearer\s+(.+)$/i);
  if (!match) {
    return res.status(401).json({
      error: 'UNAUTHORIZED',
      message: 'Missing or malformed Authorization: Bearer <key> header.',
    });
  }
  const token = match[1].trim();
  if (config.apiKeys.length === 0 || !config.apiKeys.includes(token)) {
    return res.status(401).json({ error: 'UNAUTHORIZED', message: 'Invalid API key.' });
  }
  next();
}
