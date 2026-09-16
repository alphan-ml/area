import { test } from 'node:test';
import assert from 'node:assert/strict';
import { validateAndNormalize, QueryRejected, ROW_CAP } from '../lib/guardrail.js';

test('a plain allow-listed SELECT passes through with LIMIT appended', () => {
  const sql = validateAndNormalize('SELECT SUM(amount_usd) FROM payments');
  assert.match(sql, new RegExp('LIMIT ' + ROW_CAP + '$'));
});

test('an existing LIMIT is left alone, not doubled', () => {
  const sql = validateAndNormalize('SELECT * FROM payments LIMIT 10');
  assert.equal((sql.match(/LIMIT/gi) || []).length, 1);
});

test('a non-SELECT statement is rejected', () => {
  assert.throws(() => validateAndNormalize('DELETE FROM payments'), QueryRejected);
});

test('a semicolon-chained second statement is rejected', () => {
  assert.throws(() => validateAndNormalize('SELECT 1 FROM payments; DROP TABLE payments'), QueryRejected);
});

test('a single trailing semicolon is tolerated', () => {
  const sql = validateAndNormalize('SELECT 1 FROM payments LIMIT 1;');
  assert.ok(!sql.includes(';'));
});

test('a non-allow-listed table is rejected', () => {
  assert.throws(() => validateAndNormalize('SELECT * FROM pg_shadow'), QueryRejected);
});

test('a view in the allow-list is accepted', () => {
  const sql = validateAndNormalize('SELECT * FROM q_totals');
  assert.ok(sql.startsWith('SELECT'));
});

test('a query with no FROM clause is rejected', () => {
  assert.throws(() => validateAndNormalize('SELECT 1'), QueryRejected);
});
