/**
 * AREA -- GET /api/facts (task W-B5). Runs the same 5 headline queries as
 * data/facts.md / src/area/cli.py's FACTS_QUERIES (kept deliberately in
 * sync -- see that file's own comment) against the real read-only
 * database, and returns each real result or an honest per-fact error.
 * Never returns a placeholder/estimated number (repo-wide D10 rule).
 */
import { runQuery } from '../lib/db.js';

export const FACTS_QUERIES = [
  ['total_dollars_2021_2025', 'SELECT SUM(amount_usd) AS total_usd FROM payments WHERE program_year BETWEEN 2021 AND 2025'],
  ['top_specialty_by_dollars', "SELECT physician_specialty, SUM(amount_usd) AS total_usd FROM payments WHERE physician_specialty IS NOT NULL GROUP BY physician_specialty ORDER BY total_usd DESC LIMIT 1"],
  ['top_state_by_dollars', "SELECT physician_state, SUM(amount_usd) AS total_usd FROM payments WHERE physician_state IS NOT NULL GROUP BY physician_state ORDER BY total_usd DESC LIMIT 1"],
  ['dollars_per_quarter_first_4', 'SELECT quarter, SUM(amount_usd) AS total_usd FROM payments WHERE quarter IS NOT NULL GROUP BY quarter ORDER BY quarter LIMIT 4'],
  ['food_and_beverage_share_pct', "SELECT ROUND(100.0 * SUM(amount_usd) FILTER (WHERE nature_of_payment = 'Food and Beverage') / NULLIF(SUM(amount_usd), 0), 2) AS food_and_beverage_share_pct FROM payments"],
];

export async function computeFacts({ pool } = {}) {
  if (!process.env.DATABASE_URL) {
    return {
      ok: false,
      error: 'DATABASE_URL is not configured',
      facts: Object.fromEntries(FACTS_QUERIES.map(([key]) => [key, null])),
    };
  }
  const facts = {};
  let anyError = null;
  for (const [key, sql] of FACTS_QUERIES) {
    const result = await runQuery(sql, { pool });
    if (!result.ok) {
      facts[key] = { error: result.error };
      anyError = anyError || result.error;
    } else {
      facts[key] = key === 'dollars_per_quarter_first_4' ? result.rows : (result.rows[0] ?? null);
    }
  }
  return { ok: !anyError, error: anyError, facts };
}

export default async function handler(req, res) {
  if (req.method !== 'GET') {
    res.status(405).json({ error: 'GET only' });
    return;
  }
  const result = await computeFacts();
  res.status(200).json(result);
}
