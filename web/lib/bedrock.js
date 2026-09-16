/**
 * Bedrock Converse wrapper (task W-B5). Same shape as model-bench's
 * web/api/run-one.js callBedrockConverse (this repo's sibling): never
 * throws, returns {text, inputTokens, outputTokens, latencyMs, error},
 * and the client is injectable so tests never call a real API.
 *
 * CONTEXT.md D18: Claude Haiku 4.5 / Sonnet 4.5 are blocked on Bedrock
 * for this AWS account (verified live from Python, W-B4) -- this module
 * defaults to Amazon Nova Lite, the real, currently-invocable model.
 */
import { BedrockRuntimeClient, ConverseCommand } from '@aws-sdk/client-bedrock-runtime';

export const DEFAULT_MODEL_ID = process.env.AREA_MODEL_ID || 'us.amazon.nova-lite-v1:0';

let _client = null;
export function getBedrockClient() {
  if (_client === null) {
    _client = new BedrockRuntimeClient({ region: process.env.AWS_REGION });
  }
  return _client;
}
export function _resetClientForTests() {
  _client = null;
}

export async function callBedrockConverse(modelId, prompt, { client, maxTokens = 500, temperature = 0 } = {}) {
  const start = Date.now();
  const bedrockClient = client || getBedrockClient();
  try {
    const command = new ConverseCommand({
      modelId,
      messages: [{ role: 'user', content: [{ text: prompt }] }],
      inferenceConfig: { temperature, maxTokens },
    });
    const response = await bedrockClient.send(command);
    return {
      text: response.output?.message?.content?.[0]?.text ?? '',
      inputTokens: response.usage?.inputTokens ?? 0,
      outputTokens: response.usage?.outputTokens ?? 0,
      latencyMs: Date.now() - start,
      error: null,
    };
  } catch (err) {
    return {
      text: '', inputTokens: 0, outputTokens: 0,
      latencyMs: Date.now() - start,
      error: String(err?.message ?? err),
    };
  }
}
