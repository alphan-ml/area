import { test } from 'node:test';
import assert from 'node:assert/strict';
import { computeFacts, FACTS_QUERIES } from '../api/facts.js';

test('reports not-configured facts (all null) when DATABASE_URL is unset', async (t) => {
  const prev = process.env.DATABASE_URL;
  delete process.env.DATABASE_URL;
  t.after(() => { if (prev !== undefined) process.env.DATABASE_URL = prev; });
  const result = await computeFacts();
  assert.equal(result.ok, false);
  for (const [key] of FACTS_QUERIES) {
    assert.equal(result.facts[key], null);
  }
});

test('returns real rows per fact when the pool is reachable', async (t) => {
  const prev = process.env.DATABASE_URL;
  process.env.DATABASE_URL = 'postgres://fake';
  t.after(() => { process.env.DATABASE_URL = prev; });
  const fakePool = { query: async (sql) => {
    if (sql.includes('quarter')) return { rows: [{ quarter: '2021Q1', total_usd: 100 }] };
    return { rows: [{ total_usd: 100 }] };
  } };
  const result = await computeFacts({ pool: fakePool });
  assert.equal(result.ok, true);
  assert.equal(result.facts.total_dollars_2021_2025.total_usd, 100);
  assert.equal(Array.isArray(result.facts.dollars_per_quarter_first_4), true);
});

test('surfaces a per-fact error without crashing the whole response', async (t) => {
  const prev = process.env.DATABASE_URL;
  process.env.DATABASE_URL = 'postgres://fake';
  t.after(() => { process.env.DATABASE_URL = prev; });
  const fakePool = { query: async () => { throw new Error('relation payments does not exist'); } };
  const result = await computeFacts({ pool: fakePool });
  assert.equal(result.ok, false);
  assert.match(result.facts.total_dollars_2021_2025.error, /does not exist/);
});
