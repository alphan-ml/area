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
 *
 * Fallback marker rule (2026-09-27, F1 diagnosis): the composer does not
 * always place the marker immediately after a number -- a one-clause
 * percentage/share sentence ("24.7% of X went to Y [q_web].") often gets
 * one marker at the end of the sentence instead of right after the
 * number. When a number has no marker immediately following it, this
 * checker now also looks for the next marker later in the text and
 * attributes it to that number ONLY when it is the single unclaimed
 * number before that marker -- if two or more unclaimed numbers precede
 * the same marker, none of them are guessed (ambiguous stays unverified,
 * same conservative default as before). This caught real false
 * negatives in production: a verified-correct number (34.05%, matching
 * its own SQL result exactly) was rejected as "unverified" purely
 * because of marker placement, not because the number was wrong.
 */

const NUMBER_PATTERN =
  /(?<currency>\$\d[\d,]*(?:\.\d+)?)|(?<comma>\d{1,3}(?:,\d{3})+(?:\.\d+)?%?)|(?<decimal>\d+\.\d+%?)|(?<![A-Za-z-])(?<bare>\d+%?)/g;
const MARKER_PATTERN = /^\s*\[([A-Za-z0-9_-]+)\]/;
const MARKER_SPAN_PATTERN = /\[[A-Za-z0-9_-]+\]/g;
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

/** Numeric leaves of a payload, including numbers stored as strings (a
 * Postgres NUMERIC column comes back from the driver as a string, e.g.
 * "34.0500000000000000", to avoid float precision loss -- treat a
 * string that parses whole as a number the same as a native number).
 * Mirrors area.tools.citation_checker._iter_numeric_leaves (Python). */
function findNumericLeaves(payload) {
  const out = [];
  const walk = (v) => {
    if (typeof v === 'number' && Number.isFinite(v)) {
      out.push(v);
      return;
    }
    if (typeof v === 'string') {
      const cleaned = v.replace(/,/g, '').trim();
      if (cleaned !== '' && Number.isFinite(Number(cleaned))) out.push(Number(cleaned));
      return;
    }
    if (v && typeof v === 'object') Object.values(v).forEach(walk);
  };
  walk(payload);
  return out;
}

/**
 * @param {string} answerText
 * @param {{evidence_id: string, payload: unknown}[]} evidence
 * @returns {{accepted: boolean, unverified: string[], numbers: object[]}}
 */
export function checkCitations(answerText, evidence) {
  const byId = new Map(evidence.map((e) => [e.evidence_id, e]));

  // All marker spans in the text, in order, with their id.
  const allMarkers = [];
  MARKER_SPAN_PATTERN.lastIndex = 0;
  let spanMatch;
  while ((spanMatch = MARKER_SPAN_PATTERN.exec(answerText)) !== null) {
    allMarkers.push({
      start: spanMatch.index,
      end: spanMatch.index + spanMatch[0].length,
      id: spanMatch[0].slice(1, -1),
    });
  }
  const insideAMarker = (pos) => allMarkers.some((s) => pos >= s.start && pos < s.end);

  // All claimed numbers not inside a marker, in order.
  const allNumbers = [];
  NUMBER_PATTERN.lastIndex = 0;
  let match;
  while ((match = NUMBER_PATTERN.exec(answerText)) !== null) {
    const raw = match[0];
    if (insideAMarker(match.index)) continue;
    if (isYear(raw, match.groups)) continue;
    allNumbers.push({ raw, start: match.index, end: match.index + raw.length, value: parseNumber(raw) });
  }

  // Pass 1: immediate adjacency (marker right after the number).
  const numberMarkerId = new Map(); // index into allNumbers -> marker id
  const claimedMarkerStarts = new Set();
  allNumbers.forEach((num, i) => {
    const rest = answerText.slice(num.end);
    const markerMatch = rest.match(MARKER_PATTERN);
    if (!markerMatch) return;
    const markerStart = num.end + rest.indexOf(markerMatch[0]);
    const marker = allMarkers.find((s) => s.start === markerStart);
    if (marker) {
      numberMarkerId.set(i, marker.id);
      claimedMarkerStarts.add(marker.start);
    }
  });

  // Pass 2: fallback -- an unclaimed marker attributed to the single
  // unclaimed number that precedes it (since the last unclaimed marker,
  // or the start of the text). Skipped when 0 or 2+ candidates.
  let boundary = 0;
  for (const marker of allMarkers) {
    if (claimedMarkerStarts.has(marker.start)) {
      boundary = marker.end;
      continue;
    }
    const candidates = [];
    allNumbers.forEach((num, i) => {
      if (numberMarkerId.has(i)) return;
      if (num.start >= boundary && num.end <= marker.start) candidates.push(i);
    });
    if (candidates.length === 1) numberMarkerId.set(candidates[0], marker.id);
    boundary = marker.end;
  }

  const numbers = allNumbers.map((num) => {
    const citedId = numberMarkerId.get(allNumbers.indexOf(num)) ?? null;
    if (!citedId) {
      return { raw_text: num.raw, value: num.value, cited_id: null, passed: false, reason: 'no citation marker follows this number' };
    }
    const ev = byId.get(citedId);
    if (!ev) {
      return { raw_text: num.raw, value: num.value, cited_id: citedId, passed: false, reason: `citation id ${JSON.stringify(citedId)} matches no evidence` };
    }
    const leaves = findNumericLeaves(ev.payload);
    const isPercent = num.raw.trim().endsWith('%');
    // A stated percentage is often derived from a raw 0-1 fraction the SQL
    // returned as-is (e.g. "food_beverage_share": 0.3405) -- the composer
    // is expected to multiply by 100 for a readable answer (F1 diagnosis,
    // 2026-09-27: this exact case, a correct 34.05% answer, was being
    // rejected because the evidence held 0.3405, not 34.05). A stated
    // percentage matches a leaf either directly, or scaled by 100 either
    // way (some generated SQL already multiplies by 100, some doesn't).
    const candidates = isPercent
      ? leaves.flatMap((leaf) => [leaf, leaf * 100, leaf / 100])
      : leaves;
    const ok = candidates.some(
      (leaf) => Math.abs(leaf - num.value) <= Math.max(Math.abs(leaf), Math.abs(num.value)) * RELATIVE_TOLERANCE || leaf === num.value
    );
    return {
      raw_text: num.raw, value: num.value, cited_id: citedId, passed: ok,
      reason: ok ? null : `${num.value} not found (within tolerance) in evidence ${JSON.stringify(citedId)}`,
    };
  });
  const unverified = numbers.filter((n) => !n.passed).map((n) => n.raw_text);
  return { accepted: unverified.length === 0, unverified, numbers };
}
