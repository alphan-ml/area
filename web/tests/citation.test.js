import { test } from 'node:test';
import assert from 'node:assert/strict';
import { checkCitations } from '../lib/citation.js';

test('a cited number matching evidence within tolerance is accepted', () => {
  const evidence = [{ evidence_id: 'q_1', payload: [{ total_usd: 100 }] }];
  const r = checkCitations('Total was $100 [q_1].', evidence);
  assert.equal(r.accepted, true);
  assert.deepEqual(r.unverified, []);
});

test('an uncited number is rejected', () => {
  const r = checkCitations('Total was $100 with no citation.', []);
  assert.equal(r.accepted, false);
  assert.equal(r.unverified.length, 1);
});

test('a citation id matching nothing is rejected', () => {
  const r = checkCitations('Total was $100 [q_missing].', []);
  assert.equal(r.accepted, false);
});

test('a bare year is not treated as a claimed number', () => {
  const evidence = [{ evidence_id: 'q_1', payload: [{ total_usd: 500 }] }];
  const r = checkCitations('In 2023, total payments were $500 [q_1].', evidence);
  assert.equal(r.accepted, true);
  assert.equal(r.numbers.length, 1);
});

test('a GLP-1 style product suffix digit is not treated as a claimed number', () => {
  const r = checkCitations('No matching data is available yet for GLP-1 records.', []);
  assert.equal(r.accepted, true);
  assert.deepEqual(r.numbers, []);
});

test('a real uncited number right after a word is still caught', () => {
  const r = checkCitations('Model v2 reported total 42 with no citation.', []);
  assert.equal(r.accepted, false);
  assert.ok(r.numbers.some((n) => n.raw_text === '42'));
});

test('a percent value matches evidence with a percent field', () => {
  const evidence = [{ evidence_id: 'q_p', payload: [{ share_pct: 5.2 }] }];
  const r = checkCitations('The share was 5.2% [q_p].', evidence);
  assert.equal(r.accepted, true);
});
