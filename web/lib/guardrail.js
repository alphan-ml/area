/**
 * Read-only SQL guardrail (task W-B5's JS side of SPEC-area.md section
 * 4.2 -- src/area/tools/query_tool.py is the Python original and stays
 * the source of truth; this is a lighter, regex/keyword-based port for
 * the Vercel /api/ask function, which cannot import Python (model-bench's
 * web/package.json rule: "Python never deploys here"). Same allow-list
 * (payments + the 3 quarterly views, per sql/001_schema.sql /
 * sql/002_views.sql) and the same 4 rejection shapes SPEC-area.md names:
 * not a single SELECT, a semicolon-chained second statement, a
 * non-allow-listed table, and no LIMIT (auto-added, not rejected, same
 * as the Python side). Defense-in-depth backstop is identical too: the
 * real query always runs as the read-only `area_reader` role.
 *
 * This is NOT a full SQL parser (same disclosed scope limit as Python's
 * own D15): it cannot resolve per-table column scoping. It catches the
 * named attack/rejection shapes; sql/001_schema.sql's SELECT-only role
 * with a 10s statement_timeout is the real backstop.
 */

export const ALLOWED_TABLES = new Set(['payments', 'q_totals', 'q_by_specialty', 'q_by_state']);
export const ROW_CAP = 5000;

export class QueryRejected extends Error {}

const FORBIDDEN_KEYWORDS =
  /\b(INSERT|UPDATE|DELETE|DROP|ALTER|TRUNCATE|GRANT|REVOKE|CREATE|CALL|EXECUTE|COPY|MERGE)\b/i;

/**
 * Validates and normalizes a candidate SQL string. Throws QueryRejected
 * with a plain-English reason on any violation; returns the (possibly
 * LIMIT-appended) SQL string on success.
 */
export function validateAndNormalize(rawSql) {
  if (typeof rawSql !== 'string' || !rawSql.trim()) {
    throw new QueryRejected('empty query');
  }
  let sql = rawSql.trim();
  // Strip one single trailing semicolon (a model's harmless habit); a
  // semicolon anywhere else means a second statement is chained on.
  if (sql.endsWith(';')) {
    sql = sql.slice(0, -1).trim();
  }
  if (sql.includes(';')) {
    throw new QueryRejected('multiple statements are not allowed');
  }
  if (!/^SELECT\b/i.test(sql)) {
    throw new QueryRejected('only a single SELECT statement is allowed');
  }
  if (FORBIDDEN_KEYWORDS.test(sql)) {
    throw new QueryRejected('only read-only SELECT queries are allowed');
  }

  const fromMatches = [...sql.matchAll(/\b(?:FROM|JOIN)\s+([A-Za-z_][A-Za-z0-9_]*)/gi)];
  if (fromMatches.length === 0) {
    throw new QueryRejected('no FROM clause found');
  }
  for (const m of fromMatches) {
    const table = m[1].toLowerCase();
    if (!ALLOWED_TABLES.has(table)) {
      throw new QueryRejected(`table ${JSON.stringify(table)} is not allow-listed`);
    }
  }

  if (!/\bLIMIT\s+\d+/i.test(sql)) {
    sql = `${sql} LIMIT ${ROW_CAP}`;
  }
  return sql;
}
