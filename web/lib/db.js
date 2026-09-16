/**
 * Postgres query helper (task W-B5). Reads DATABASE_URL at call time (not
 * import time) so a function that never touches the database still cold-
 * starts fine with no DB configured -- same not-configured-is-not-a-crash
 * discipline as area.tools.query_tool's own database_url handling.
 * Client is injectable so tests never open a real connection.
 */
import pg from 'pg';

const { Pool } = pg;
let _pool = null;

export function getPool() {
  const databaseUrl = process.env.DATABASE_URL;
  if (!databaseUrl) return null;
  if (_pool === null) {
    _pool = new Pool({ connectionString: databaseUrl, max: 3 });
  }
  return _pool;
}

export function _resetPoolForTests() {
  _pool = null;
}

/**
 * Runs one read-only SQL query. @param {{pool?: {query: Function}}} [deps]
 * Returns {ok, rows, error}. Never throws.
 */
export async function runQuery(sql, { pool } = {}) {
  const activePool = pool || getPool();
  if (!activePool) {
    return { ok: false, rows: null, error: 'DATABASE_URL is not configured' };
  }
  try {
    const result = await activePool.query(sql);
    return { ok: true, rows: result.rows, error: null };
  } catch (err) {
    return { ok: false, rows: null, error: String(err?.message ?? err) };
  }
}
