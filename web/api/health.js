/**
 * AREA -- GET /api/health (task W-B5). Reports whether the database is
 * configured/reachable, the real payments row count when it is, and
 * which model this deployment's /api/ask would call. Never fakes a
 * reachable/row-count value -- a missing DATABASE_URL or a real
 * connection failure is reported as such, same honesty rule as the CLI's
 * `area tools-test` (src/area/cli.py).
 */
import { runQuery } from '../lib/db.js';
import { DEFAULT_MODEL_ID } from '../lib/bedrock.js';

export async function computeHealth({ pool } = {}) {
  const databaseConfigured = Boolean(process.env.DATABASE_URL);
  let database = { configured: databaseConfigured, reachable: false, payments_row_count: null, error: null };
  if (databaseConfigured) {
    const result = await runQuery('SELECT COUNT(*)::int AS row_count FROM payments', { pool });
    if (result.ok) {
      database = { configured: true, reachable: true, payments_row_count: result.rows[0].row_count, error: null };
    } else {
      database = { configured: true, reachable: false, payments_row_count: null, error: result.error };
    }
  }
  return {
    ok: true,
    service: 'area',
    database,
    model: {
      adapter: 'bedrock',
      model_id: DEFAULT_MODEL_ID,
      aws_region_configured: Boolean(process.env.AWS_REGION),
    },
    timestamp: new Date().toISOString(),
  };
}

/** @param {import('node:http').IncomingMessage} req
 *  @param {import('node:http').ServerResponse & {status: Function, json: Function}} res */
export default async function handler(req, res) {
  if (req.method !== 'GET') {
    res.status(405).json({ error: 'GET only' });
    return;
  }
  const health = await computeHealth();
  res.status(200).json(health);
}
