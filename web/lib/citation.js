/**
 * Citation checker (task W-B5's JS port of src/area/tools/citation_checker.py --
 * that file is the source of truth and stays the source of
 * defense-in-depth; this is the same wire format and the same
 * number-extraction rule, used by /api/ask before it returns an answer).
 *
 * Marker format (CONTEXT.md D12): a number immediately followed by e.g.
 * [q_ab12cd34]. Tolerance: 0.5% relative, or an exact string match.
 * Years (bare 1900-2099 integers) are excluded. D19's GLP-1 fix is
 * carried over here too: a digit glued onto a preceding letter/hyphen
 * (e.g. the "1" in "GLP-1") is not treated as a claimed number.
 */

const NUMBER_PATTERN =
  /(?<currency>\$\d[\d,]*(?:\.\d+)?)|(?<comma>\d{1,3}(?:,\d{3})+(?:\.\d+)?%?)|(?<decimal>\d+\.\d+%?)|(?<![A-Za-z-])(?<bare>\d+%?)/g;
const MARKER_PATTERN = /^\s*\[([A-Za-z0-9_-]+)\]/;
const RELATIVE_TOLERANCE = 0.005;

function parseNumber(raw) {
  return Number(raw.replace(/[$,%]/g, ''));
}

function isYear(raw, matchGroups) {
  if (matchGroups.currency || matchGroups.comma || matchGroups.decimal) return false;
  if (!/^\d+$/.test(raw)) return false;
  const n = Number(raw);
  return n >= 1900 && n <= 2099;
}

function findNumericLeaves(payload) {
  const out = [];
  const walk = (v) => {
    if (typeof v === 'number' && Number.isFinite(v)) out.push(v);
    else if (v && typeof v === 'object') Object.values(v).forEach(walk);
  };
  walk(payload);
  return out;
}

/**
 * @param {string} answerText
 * @param {{evidence_id: string, payload: unknown}[]} evidence
 * @returns {{accepted: boolean, unverified: string[], numbers: object[]}}
 */
const MARKER_SPAN_PATTERN = /\[[A-Za-z0-9_-]+\]/g;

function insideAMarker(pos, spans) {
  return spans.some(([start, end]) => pos >= start && pos < end);
}

export function checkCitations(answerText, evidence) {
  const byId = new Map(evidence.map((e) => [e.evidence_id, e]));
  const numbers = [];
  const markerSpans = [];
  let spanMatch;
  MARKER_SPAN_PATTERN.lastIndex = 0;
  while ((spanMatch = MARKER_SPAN_PATTERN.exec(answerText)) !== null) {
    markerSpans.push([spanMatch.index, spanMatch.index + spanMatch[0].length]);
  }
  let match;
  NUMBER_PATTERN.lastIndex = 0;
  while ((match = NUMBER_PATTERN.exec(answerText)) !== null) {
    const raw = match[0];
    if (insideAMarker(match.index, markerSpans)) continue;
    if (isYear(raw, match.groups)) continue;
    const claimed = parseNumber(raw);
    const rest = answerText.slice(match.index + raw.length);
    const markerMatch = rest.match(MARKER_PATTERN);
    if (!markerMatch) {
      numbers.push({ raw_text: raw, value: claimed, cited_id: null, passed: false, reason: 'no citation marker immediately follows this number' });
      continue;
    }
    const citedId = markerMatch[1];
    const ev = byId.get(citedId);
    if (!ev) {
      numbers.push({ raw_text: raw, value: claimed, cited_id: citedId, passed: false, reason: `citation id ${JSON.stringify(citedId)} matches no evidence` });
      continue;
    }
    const leaves = findNumericLeaves(ev.payload);
    const ok = leaves.some((leaf) => Math.abs(leaf - claimed) <= Math.max(Math.abs(leaf), Math.abs(claimed)) * RELATIVE_TOLERANCE || leaf === claimed);
    numbers.push({
      raw_text: raw, value: claimed, cited_id: citedId, passed: ok,
      reason: ok ? null : `${claimed} not found (within tolerance) in evidence ${JSON.stringify(citedId)}`,
    });
  }
  const unverified = numbers.filter((n) => !n.passed).map((n) => n.raw_text);
  return { accepted: unverified.length === 0, unverified, numbers };
}
