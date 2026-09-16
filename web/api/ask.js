/**
 * AREA -- POST /api/ask (task W-B5). {question: string} -> a cited
 * answer over the real payments data, computed live: one Bedrock call
 * writes SQL against the allow-listed schema, lib/guardrail.js validates
 * it (the same 4 rejection shapes as src/area/tools/query_tool.py, ported
 * to JS -- see that file's header comment for why Python doesn't deploy
 * here), the SQL runs read-only, a second Bedrock call composes a cited
 * answer from the real rows, and lib/citation.js verifies every number
 * before the response is returned. An answer with an unverified number
 * is still returned (with accepted:false and the unverified list) --
 * SPEC-area.md section 1's hard gate is the caller's job to honor by not
 * displaying it as trustworthy, same contract as area.agent.loop's
 * AgentTrace.accepted (Python, W-B4).
 *
 * Never invents a number: a question that needs data outside the
 * allow-listed schema, an empty result set, or a database/model error
 * all come back as an honest, uncited "no data" style answer or a plain
 * `error` field -- never a fabricated figure.
 */
import { runQuery } from '../lib/db.js';
import { callBedrockConverse, DEFAULT_MODEL_ID } from '../lib/bedrock.js';
import { validateAndNormalize, QueryRejected } from '../lib/guardrail.js';
import { checkCitations } from '../lib/citation.js';

export const MAX_QUESTION_LENGTH = 500;

const SCHEMA_TEXT = `Allow-listed schema (read-only) -- reference ONLY these tables/columns:

payments(record_id, program_year, payment_date, quarter, physician_id,
         physician_specialty, physician_state, manufacturer, product,
         product_generic, nature_of_payment, amount_usd, matched_field,
         source_year_dataset)
q_totals(quarter, product, manufacturer, total_usd, payment_count)
q_by_specialty(quarter, physician_specialty, total_usd, payment_count)
q_by_state(quarter, physician_state, total_usd, payment_count)

Write exactly one SELECT statement, no semicolons, no other tables. Include a LIMIT (<= 5000).`;

function sqlPrompt(question) {
  return `${SCHEMA_TEXT}\n\nQuestion: ${question}\n\nRespond with ONLY the SQL, no explanation, no markdown fences.`;
}

function composePrompt(question, evidenceId, rows) {
  const rowsJson = JSON.stringify(rows).slice(0, 4000);
  return [
    'Write a short, plain-English answer to this question, using ONLY the evidence rows given -- never invent, round, or estimate a number that is not directly present in them.',
    '',
    `Question: ${question}`,
    '',
    `Evidence rows (evidence_id ${evidenceId}):`,
    rowsJson,
    '',
    'Rules:',
    `- Every number you state (except a bare year) must be immediately followed by its citation marker, e.g. $1,234.56 [${evidenceId}] or 24.7% [${evidenceId}].`,
    '- If the rows are empty, or every relevant total is null, say plainly that no matching data is available yet -- do not invent a number.',
    '- Answer in 2-4 sentences. No markdown.',
  ].join('\n');
}

/** @param {{pool?, bedrockClient?, modelId?}} [deps] */
export async function answerQuestion(question, deps = {}) {
  const modelId = deps.modelId || DEFAULT_MODEL_ID;
  const client = deps.bedrockClient;
  let inputTokens = 0;
  let outputTokens = 0;

  const sqlCall = await callBedrockConverse(modelId, sqlPrompt(question), { client });
  inputTokens += sqlCall.inputTokens;
  outputTokens += sqlCall.outputTokens;
  if (sqlCall.error) {
    return { ok: false, error: `model call failed: ${sqlCall.error}`, model_id: modelId, input_tokens: inputTokens, output_tokens: outputTokens };
  }

  let sql;
  try {
    sql = validateAndNormalize(sqlCall.text);
  } catch (err) {
    if (err instanceof QueryRejected) {
      return { ok: false, error: `query rejected: ${err.message}`, sql: sqlCall.text, model_id: modelId, input_tokens: inputTokens, output_tokens: outputTokens };
    }
    throw err;
  }

  const queryResult = await runQuery(sql, { pool: deps.pool });
  if (!queryResult.ok) {
    return { ok: false, error: `query execution failed: ${queryResult.error}`, sql, model_id: modelId, input_tokens: inputTokens, output_tokens: outputTokens };
  }

  const evidenceId = 'q_web';
  const composeCall = await callBedrockConverse(modelId, composePrompt(question, evidenceId, queryResult.rows), { client });
  inputTokens += composeCall.inputTokens;
  outputTokens += composeCall.outputTokens;
  if (composeCall.error) {
    return { ok: false, error: `model call failed: ${composeCall.error}`, sql, model_id: modelId, input_tokens: inputTokens, output_tokens: outputTokens };
  }

  const answerText = composeCall.text.trim();
  const evidence = [{ evidence_id: evidenceId, kind: 'sql_rows', payload: queryResult.rows }];
  const verdict = checkCitations(answerText, evidence);

  return {
    ok: true,
    question,
    sql,
    row_count: queryResult.rows.length,
    answer: answerText,
    accepted: verdict.accepted,
    unverified: verdict.unverified,
    evidence,
    model_id: modelId,
    input_tokens: inputTokens,
    output_tokens: outputTokens,
  };
}

export default async function handler(req, res) {
  if (req.method !== 'POST') {
    res.status(405).json({ error: 'POST only' });
    return;
  }
  const question = typeof req.body?.question === 'string' ? req.body.question.trim() : '';
  if (!question) {
    res.status(400).json({ error: 'question (non-empty string) is required' });
    return;
  }
  if (question.length > MAX_QUESTION_LENGTH) {
    res.status(400).json({ error: `question exceeds ${MAX_QUESTION_LENGTH} characters` });
    return;
  }
  if (!process.env.DATABASE_URL) {
    res.status(200).json({ ok: false, error: 'DATABASE_URL is not configured', question });
    return;
  }
  const result = await answerQuestion(question);
  res.status(result.ok ? 200 : 502).json(result);
}
