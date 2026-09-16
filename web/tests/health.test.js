import { test } from 'node:test';
import assert from 'node:assert/strict';
import { computeHealth } from '../api/health.js';

test('reports database not configured when DATABASE_URL is unset', async (t) => {
  const prev = process.env.DATABASE_URL;
  delete process.env.DATABASE_URL;
  t.after(() => { if (prev !== undefined) process.env.DATABASE_URL = prev; });
  const health = await computeHealth();
  assert.equal(health.ok, true);
  assert.equal(health.database.configured, false);
  assert.equal(health.database.reachable, false);
});

test('reports a real row count when the pool is reachable', async (t) => {
  const prev = process.env.DATABASE_URL;
  process.env.DATABASE_URL = 'postgres://fake';
  t.after(() => { process.env.DATABASE_URL = prev; });
  const fakePool = { query: async () => ({ rows: [{ row_count: 114032 }] }) };
  const health = await computeHealth({ pool: fakePool });
  assert.equal(health.database.reachable, true);
  assert.equal(health.database.payments_row_count, 114032);
});

test('reports an honest error when the database is configured but unreachable', async (t) => {
  const prev = process.env.DATABASE_URL;
  process.env.DATABASE_URL = 'postgres://fake';
  t.after(() => { process.env.DATABASE_URL = prev; });
  const fakePool = { query: async () => { throw new Error('connection refused'); } };
  const health = await computeHealth({ pool: fakePool });
  assert.equal(health.database.reachable, false);
  assert.match(health.database.error, /connection refused/);
});
