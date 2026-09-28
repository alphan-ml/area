"""Citation Checker (SPEC-area.md section 4.4). A hard gate per section 1's
non-negotiables: "an answer with an unverified number is not shown; the
visitor sees the verifier's reason and the agent's best cited partial
answer" -- the agent loop (task B4) enforces that behavior using this
module's verdict; this module only decides, per number, pass or fail.

Design notes (disclosed judgment call -- the spec sketches the mechanism
in sections 4.4 and 5.3 but doesn't fully pin down the wire format; see
CONTEXT.md decision D12 for the summary the owner should review):

- The composer is expected to write inline citation markers immediately
  after a number, e.g. "$1,234.56 [q_ab12cd34]" or "24.7% [d_share1]".
  A marker id is looked up first against `evidence` (by `evidence_id`),
  then against `derivations` (by `derivation_id`). A number with no
  marker immediately following it, or whose marker id matches neither,
  fails with a stated reason.
- "Equals a value in a cited evidence payload within rounding tolerance
  (0.5% relative or the displayed precision)" (section 4.4): the payload
  is searched recursively (it may be a list of SQL row dicts, a forecast
  dict, or a doc string) for any numeric leaf within tolerance of the
  claimed number.
- "The result of a stated formula over passed values (the checker
  recomputes `+ - * / %` expressions the composer emits in a derivations
  list)" (section 4.4): each derivation is `{derivation_id, expression,
  result, cites}` -- `expression` is arithmetic over literal numbers
  (e.g. "1234.56 / 5000 * 100"), recomputed with a restricted AST
  evaluator (no names, no calls -- see `_safe_eval`) and compared to the
  stated `result`. A mismatch there is a "wrong derivation" (spec's own
  named test case) and fails regardless of what the answer text claims.
- "Years excluded from the check": any bare 4-digit integer in
  1900-2099 with no currency/percent sign, no thousands separator, and
  no decimal point is treated as a year, not a claim, and is never
  extracted as a number to verify.
"""

from __future__ import annotations

import ast
import operator
import re
from dataclasses import dataclass, field
from typing import Any

from area.tools import Evidence, Tool, ToolResult

RELATIVE_TOLERANCE = 0.005  # 0.5%, per spec section 4.4

_NUMBER_PATTERN = re.compile(
    r"(?P<currency>\$\d[\d,]*(?:\.\d+)?)"
    r"|(?P<comma_count>\d{1,3}(?:,\d{3})+(?:\.\d+)?%?)"
    r"|(?P<plain_decimal>\d+\.\d+%?)"
    # (?<![A-Za-z-]) excludes a digit glued onto a preceding letter/hyphen,
    # e.g. the "1" in "GLP-1" (a product-name suffix, not a claimed
    # number) -- found for real running area evals (task W-B4) against a
    # live Bedrock composer answer that mentioned "GLP-1" with no other
    # number in it, which this pattern was otherwise flagging as an
    # uncited claim. Currency/comma/decimal forms already can't collide
    # with a product-name suffix (no $, comma, or decimal point in one),
    # so only bare_int needs the guard.
    r"|(?<![A-Za-z-])(?P<bare_int>\d+%?)"
)
_MARKER_PATTERN = re.compile(r"\s*\[([A-Za-z0-9_\-]+)\]")
_MARKER_SPAN_PATTERN = re.compile(r"\[[A-Za-z0-9_\-]+\]")
_YEAR_RANGE = range(1900, 2100)

_SAFE_BINOPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Mod: operator.mod,
}


@dataclass
class Derivation:
    derivation_id: str
    expression: str
    result: float
    cites: list[str] = field(default_factory=list)


@dataclass
class NumberVerdict:
    raw_text: str
    value: float
    cited_id: str | None
    passed: bool
    reason: str


@dataclass
class CitationCheckResult:
    accepted: bool
    numbers: list[NumberVerdict]
    unverified: list[str]


class SafeEvalError(ValueError):
    pass


def _safe_eval(expression: str) -> float:
    """Evaluates a `+ - * / %` arithmetic expression over numeric
    literals only (spec section 4.4). Rejects anything that isn't a
    number, a unary +/-, or one of the five binary operators -- no
    names, no calls, no attribute access, no comparisons. This is
    intentionally NOT Python's eval(): a composer-emitted expression
    string is untrusted input."""
    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError as exc:
        raise SafeEvalError(f"not a valid expression: {expression!r}") from exc

    def _walk(node: ast.AST) -> float:
        if isinstance(node, ast.Expression):
            return _walk(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return float(node.value)
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
            value = _walk(node.operand)
            return value if isinstance(node.op, ast.UAdd) else -value
        if isinstance(node, ast.BinOp) and type(node.op) in _SAFE_BINOPS:
            return _SAFE_BINOPS[type(node.op)](_walk(node.left), _walk(node.right))
        raise SafeEvalError(f"disallowed expression syntax near {ast.dump(node)}")

    return _walk(tree)


def _looks_like_year(raw: str) -> bool:
    if not raw.isdigit() or len(raw) != 4:
        return False
    return int(raw) in _YEAR_RANGE


def _parse_number(raw: str) -> float:
    cleaned = raw.replace("$", "").replace(",", "").replace("%", "")
    return float(cleaned)


def _decimals_in(raw: str) -> int:
    cleaned = raw.replace("$", "").replace(",", "").replace("%", "")
    return len(cleaned.split(".")[1]) if "." in cleaned else 0


def _numbers_close(claimed: float, candidate: float, claimed_raw: str) -> bool:
    if candidate == claimed:
        return True
    relative_ok = abs(candidate - claimed) <= RELATIVE_TOLERANCE * abs(candidate or claimed or 1)
    if relative_ok:
        return True
    # "displayed precision": rounding the candidate to the claimed number's
    # own decimal places lands exactly on the claimed number.
    decimals = _decimals_in(claimed_raw)
    return round(candidate, decimals) == claimed


def _iter_numeric_leaves(payload: Any):
    """Walks a payload (sql_rows list-of-dicts, a forecast dict, a doc
    string, or any nesting of these) and yields every numeric value found
    -- including numbers embedded in strings, so a "doc" evidence payload
    (plain text) is searchable the same way as structured payloads."""
    if payload is None:
        return
    if isinstance(payload, bool):
        return
    if isinstance(payload, (int, float)):
        yield float(payload)
        return
    if isinstance(payload, str):
        for match in _NUMBER_PATTERN.finditer(payload):
            raw = match.group(0)
            if _looks_like_year(raw.rstrip("%")):
                continue
            try:
                yield _parse_number(raw)
            except ValueError:
                continue
        return
    if isinstance(payload, dict):
        for value in payload.values():
            yield from _iter_numeric_leaves(value)
        return
    if isinstance(payload, (list, tuple)):
        for item in payload:
            yield from _iter_numeric_leaves(item)
        return


def _find_in_evidence(claimed: float, claimed_raw: str, evidence: Evidence) -> bool:
    """A stated percentage is often derived from a raw 0-1 fraction the SQL
    returned as-is (e.g. a column named "..._share" holding 0.3405) -- the
    composer is expected to multiply by 100 for a readable answer (F1
    diagnosis, 2026-09-27: a correct 34.05% answer was being rejected
    because the evidence held 0.3405, not 34.05). When the claimed number
    is a percentage, also accept a leaf scaled by 100 either way, since
    some generated SQL already multiplies by 100 and some does not."""
    is_percent = claimed_raw.rstrip().endswith("%")
    for candidate in _iter_numeric_leaves(evidence.payload):
        scaled = (candidate, candidate * 100, candidate / 100) if is_percent else (candidate,)
        if any(_numbers_close(claimed, s, claimed_raw) for s in scaled):
            return True
    return False


def check(
    answer_text: str,
    evidence: list[Evidence],
    derivations: list[Derivation] | None = None,
) -> CitationCheckResult:
    """Checks every non-year number in answer_text against a citation
    marker immediately following it (see module docstring)."""
    derivations = derivations or []
    evidence_by_id = {e.evidence_id: e for e in evidence}
    derivations_by_id = {d.derivation_id: d for d in derivations}

    all_markers = [
        (m.start(), m.end(), m.group(1)) for m in re.finditer(r"\[([A-Za-z0-9_\-]+)\]", answer_text)
    ]

    def _inside_a_marker(pos: int) -> bool:
        return any(start <= pos < end for start, end, _ in all_markers)

    numbers: list[tuple[str, float, int, int]] = []  # raw, claimed, start, end
    for match in _NUMBER_PATTERN.finditer(answer_text):
        if _inside_a_marker(match.start()):
            continue  # a digit inside "[q_ab12cd34]" is part of the id, not a claimed number
        raw = match.group(0)
        if _looks_like_year(raw.rstrip("%")):
            continue
        try:
            claimed = _parse_number(raw)
        except ValueError:
            continue
        numbers.append((raw, claimed, match.start(), match.end()))

    # Pass 1: immediate adjacency (marker right after the number).
    number_marker_id: dict[int, str] = {}
    claimed_marker_starts: set[int] = set()
    for i, (_raw, _claimed, _start, end) in enumerate(numbers):
        marker_match = _MARKER_PATTERN.match(answer_text, end)
        if not marker_match:
            continue
        number_marker_id[i] = marker_match.group(1)
        claimed_marker_starts.add(marker_match.start())

    # Pass 2 (F1 diagnosis, 2026-09-27): a composer does not always place
    # the marker immediately after a number -- a one-clause percentage
    # sentence ("24.7% of X went to Y [q_web].") often gets one marker at
    # the end of the sentence instead. An unclaimed marker is attributed
    # to the single unclaimed number before it (since the previous
    # unclaimed marker, or the start of the text); 0 or 2+ candidates are
    # left ambiguous and not guessed, same conservative default as before.
    boundary = 0
    for marker_start, marker_end, marker_id in all_markers:
        if marker_start in claimed_marker_starts:
            boundary = marker_end
            continue
        candidates = [
            i
            for i, (_raw, _claimed, n_start, n_end) in enumerate(numbers)
            if i not in number_marker_id and n_start >= boundary and n_end <= marker_start
        ]
        if len(candidates) == 1:
            number_marker_id[candidates[0]] = marker_id
        boundary = marker_end

    verdicts: list[NumberVerdict] = []
    for i, (raw, claimed, _start, _end) in enumerate(numbers):
        cited_id = number_marker_id.get(i)

        if cited_id is None:
            verdicts.append(
                NumberVerdict(raw, claimed, None, False, "no citation marker follows this number")
            )
            continue

        if cited_id in evidence_by_id:
            ev = evidence_by_id[cited_id]
            if _find_in_evidence(claimed, raw, ev):
                verdicts.append(
                    NumberVerdict(raw, claimed, cited_id, True, "matches cited evidence")
                )
            else:
                verdicts.append(
                    NumberVerdict(
                        raw, claimed, cited_id, False,
                        f"no value in evidence {cited_id!r} matches {raw!r} within tolerance",
                    )
                )
            continue

        if cited_id in derivations_by_id:
            deriv = derivations_by_id[cited_id]
            try:
                recomputed = _safe_eval(deriv.expression)
            except SafeEvalError as exc:
                verdicts.append(
                    NumberVerdict(
                        raw, claimed, cited_id, False, f"derivation expression invalid: {exc}"
                    )
                )
                continue
            if abs(recomputed - deriv.result) > 1e-6 * max(abs(deriv.result), 1e-9):
                verdicts.append(
                    NumberVerdict(
                        raw, claimed, cited_id, False,
                        f"derivation {cited_id!r} claims {deriv.result!r} but "
                        f"{deriv.expression!r} recomputes to {recomputed!r}",
                    )
                )
                continue
            if _numbers_close(claimed, deriv.result, raw):
                verdicts.append(
                    NumberVerdict(raw, claimed, cited_id, True, "matches recomputed derivation")
                )
            else:
                verdicts.append(
                    NumberVerdict(
                        raw, claimed, cited_id, False,
                        f"derivation {cited_id!r} recomputes to {deriv.result!r}, not {raw!r}",
                    )
                )
            continue

        verdicts.append(
            NumberVerdict(
                raw, claimed, cited_id, False,
                f"citation id {cited_id!r} matches no evidence or derivation",
            )
        )

    unverified = [v.raw_text for v in verdicts if not v.passed]
    return CitationCheckResult(accepted=not unverified, numbers=verdicts, unverified=unverified)


def run(args: dict) -> ToolResult:
    """Tool.fn entry point (SPEC-area.md section 4). args:
    {answer_text: str, evidence: list[dict], derivations: list[dict] | None}
    -- dicts, not dataclasses, since this is the JSON-schema-validated
    boundary a real tool call crosses; converted to dataclasses here."""
    answer_text = args.get("answer_text", "")
    evidence = [
        Evidence(e["evidence_id"], e.get("kind", "doc"), e.get("payload"), e.get("provenance", {}))
        for e in args.get("evidence", [])
    ]
    derivations = [
        Derivation(d["derivation_id"], d["expression"], d["result"], d.get("cites", []))
        for d in args.get("derivations", []) or []
    ]
    try:
        result = check(answer_text, evidence, derivations)
    except Exception as exc:  # defensive: a tool must return ToolResult, never raise
        return ToolResult(ok=False, data=None, evidence=[], error=str(exc))
    return ToolResult(
        ok=True,
        data={
            "accepted": result.accepted,
            "unverified": result.unverified,
            "numbers": [
                {
                    "raw_text": v.raw_text,
                    "value": v.value,
                    "cited_id": v.cited_id,
                    "passed": v.passed,
                    "reason": v.reason,
                }
                for v in result.numbers
            ],
        },
        evidence=[],
        error=None,
    )


TOOL = Tool(
    name="citation_checker",
    description=(
        "Checks every number in a drafted answer against its inline citation "
        "marker, verifying it matches the cited evidence (or a stated "
        "derivation) within tolerance. Years are not checked. Use this before "
        "showing any answer to a visitor -- SPEC-area.md section 1's hard gate."
    ),
    input_schema={
        "type": "object",
        "required": ["answer_text", "evidence"],
        "properties": {
            "answer_text": {"type": "string"},
            "evidence": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["evidence_id", "payload"],
                    "properties": {
                        "evidence_id": {"type": "string"},
                        "kind": {"type": "string", "enum": ["sql_rows", "forecast", "doc"]},
                        "payload": {},
                        "provenance": {"type": "object"},
                    },
                },
            },
            "derivations": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["derivation_id", "expression", "result"],
                    "properties": {
                        "derivation_id": {"type": "string"},
                        "expression": {"type": "string"},
                        "result": {"type": "number"},
                        "cites": {"type": "array", "items": {"type": "string"}},
                    },
                },
            },
        },
    },
    fn=run,
)
