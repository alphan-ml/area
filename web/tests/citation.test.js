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

test('a single number in a sentence is matched to a marker at the end of the sentence (F1 diagnosis)', () => {
  const evidence = [{ evidence_id: 'q_web', payload: [{ food_beverage_share: '0.34045904745976102052' }] }];
  const r = checkCitations(
    '34.05% of GLP-1 manufacturer payment dollars were categorized as Food and Beverage [q_web].',
    evidence
  );
  assert.equal(r.accepted, true, JSON.stringify(r));
  assert.deepEqual(r.unverified, []);
});

test('a numeric-string evidence value (Postgres NUMERIC) is matched like a native number', () => {
  const evidence = [{ evidence_id: 'q_web', payload: [{ percentage_other_manufacturers: '24.5300000000000000' }] }];
  const r = checkCitations(
    '24.53% of matched GLP-1 payment dollars went to manufacturers other than Novo Nordisk and Eli Lilly [q_web].',
    evidence
  );
  assert.equal(r.accepted, true, JSON.stringify(r));
});

test('two unclaimed numbers before one trailing marker stay unverified (ambiguous, not guessed)', () => {
  const evidence = [{ evidence_id: 'q_web', payload: [{ a: 10, b: 20 }] }];
  const r = checkCitations('The values were 10 and 20, both from the same query [q_web].', evidence);
  assert.equal(r.accepted, false);
  assert.equal(r.unverified.length, 2);
});
