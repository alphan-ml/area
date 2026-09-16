import { test } from 'node:test';
import assert from 'node:assert/strict';
import { answerQuestion } from '../api/ask.js';

function scriptedClient(replies) {
  const queue = [...replies];
  return {
    send: async () => {
      const text = queue.shift() ?? '';
      return {
        output: { message: { content: [{ text }] } },
        usage: { inputTokens: text.length, outputTokens: 5 },
      };
    },
  };
}

test('answers with a cited, accepted answer end to end', async () => {
  const client = scriptedClient([
    'SELECT SUM(amount_usd) AS total_usd FROM payments',
    'The total was $42.00 [q_web].',
  ]);
  const pool = { query: async () => ({ rows: [{ total_usd: 42 }] }) };
  const result = await answerQuestion('What was the total?', { pool, bedrockClient: client, modelId: 'm' });
  assert.equal(result.ok, true);
  assert.equal(result.accepted, true);
  assert.equal(result.answer, 'The total was $42.00 [q_web].');
  assert.equal(result.row_count, 1);
});

test('rejects a non-SELECT SQL from the model without querying the database', async () => {
  const client = scriptedClient(['DELETE FROM payments']);
  let queried = false;
  const pool = { query: async () => { queried = true; return { rows: [] }; } };
  const result = await answerQuestion('drop it', { pool, bedrockClient: client, modelId: 'm' });
  assert.equal(result.ok, false);
  assert.match(result.error, /query rejected/);
  assert.equal(queried, false);
});

test('surfaces an unaccepted answer (uncited number) rather than hiding it', async () => {
  const client = scriptedClient([
    'SELECT SUM(amount_usd) AS total_usd FROM payments',
    'The total was $99.00 with no citation.',
  ]);
  const pool = { query: async () => ({ rows: [{ total_usd: 42 }] }) };
  const result = await answerQuestion('q', { pool, bedrockClient: client, modelId: 'm' });
  assert.equal(result.ok, true);
  assert.equal(result.accepted, false);
  assert.ok(result.unverified.length > 0);
});

test('surfaces a query execution failure honestly', async () => {
  const client = scriptedClient(['SELECT SUM(amount_usd) AS total_usd FROM payments']);
  const pool = { query: async () => { throw new Error('relation payments does not exist'); } };
  const result = await answerQuestion('q', { pool, bedrockClient: client, modelId: 'm' });
  assert.equal(result.ok, false);
  assert.match(result.error, /query execution failed/);
});
