"""Query tool (SPEC-area.md section 4.2): question -> SQL (LLM-written,
validated) -> rows, query_id.

Step 1 (model writes SQL) is injectable: real callers get a default
generate_sql() that calls the configured model provider (area.providers,
vendored from model-bench -- see that package's docstring), but the
guardrail tests (this task's own explicit scope, section 10) inject a
fake generate_sql callable so the validator itself is fully testable
with zero secrets and zero network -- the same dependency-injection
pattern pull_open_payments.py uses for its HTTP calls (W-B1).

Steps 2-4 (validate, execute, return) are the guardrail spec literally
describes:
  - reject anything that is not a single SELECT
  - reject a reference to a non-allow-listed table/column
  - add "LIMIT 5000" if the query has no LIMIT
  - reject ';'-chained multiple statements
  - execute with area_reader, return rows + evidence_id "q_<sha8 of sql>"

The validator (validate_and_normalize) is defense-in-depth, not the only
guardrail: sql/001_schema.sql's area_reader role is SELECT-only with a
10s statement_timeout, so even a validator gap is bounded by what a
read-only role with a timeout can do (see that file's own comment on
this). Disclosed judgment call (CONTEXT.md decision D15): the
validator checks tables/views against an exact allow-list and flags any
other bare identifier that isn't a known column, alias, keyword, or
function name -- it does not fully resolve per-table column scoping
(e.g. it would not catch `payments.total_usd`, a column that doesn't
exist on that table but does exist on q_totals), because a real SQL
semantic analyzer is out of scope for a v0 guardrail whose job is to
catch the test list's named attack shapes (injection strings,
UPDATE/DELETE, multi-statement, a genuinely unknown column, missing
LIMIT), not to be a full Postgres query planner.
"""

from __future__ import annotations

import hashlib
import os
import time
from typing import Callable

import sqlparse
from sqlparse import tokens as T
from sqlparse.sql import Identifier, IdentifierList

from area.tools import Evidence, Tool, ToolResult

ALLOWED_TABLES: dict[str, set[str]] = {
    "payments": {
        "record_id", "program_year", "payment_date", "quarter", "physician_id",
        "physician_specialty", "physician_state", "manufacturer", "product",
        "product_generic", "nature_of_payment", "amount_usd", "matched_field",
        "source_year_dataset",
    },
    "q_totals": {"quarter", "product", "manufacturer", "total_usd", "payment_count"},
    "q_by_specialty": {"quarter", "physician_specialty", "total_usd", "payment_count"},
    "q_by_state": {"quarter", "physician_state", "total_usd", "payment_count"},
}
ALL_ALLOWED_COLUMNS = {col for cols in ALLOWED_TABLES.values() for col in cols}
ROW_CAP = 5000

_KEYWORD_TOKEN_TYPES = (T.Keyword, T.Keyword.DML, T.Keyword.DDL, T.Keyword.CTE, T.Wildcard)
_ALLOWED_FUNCTIONS = {
    "count", "sum", "avg", "min", "max", "coalesce", "round", "cast", "extract",
    "date_trunc", "lower", "upper", "abs", "nullif",
}

SCHEMA_TEXT = """\
Allow-listed schema (read-only) -- you may reference ONLY these tables/views and columns:

payments(record_id, program_year, payment_date, quarter, physician_id,
         physician_specialty, physician_state, manufacturer, product,
         product_generic, nature_of_payment, amount_usd, matched_field,
         source_year_dataset)

q_totals(quarter, product, manufacturer, total_usd, payment_count)
q_by_specialty(quarter, physician_specialty, total_usd, payment_count)
q_by_state(quarter, physician_state, total_usd, payment_count)

Allowed functions: count, sum, avg, min, max, coalesce, nullif, round,
cast, extract, date_trunc, lower, upper, abs. FILTER (WHERE ...) after an
aggregate (e.g. SUM(x) FILTER (WHERE y = 'z')) is allowed.

Write exactly one SELECT statement. No semicolons, no other statement
types, no tables/columns outside this list. Include a LIMIT clause
(<= 5000 rows).
"""


class QueryRejected(ValueError):
    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


def _flatten_names(statement: sqlparse.sql.Statement):
    """Yields (value, is_followed_by_paren) for every T.Name-ish leaf
    token in the statement, in order."""
    tokens = list(statement.flatten())
    for i, tok in enumerate(tokens):
        if tok.ttype in (T.Name, T.Name.Builtin) or (
            tok.ttype is None and hasattr(tok, "get_real_name")
        ):
            next_nonspace = next(
                (t for t in tokens[i + 1 :] if t.ttype not in (T.Whitespace, T.Newline)), None
            )
            is_paren = next_nonspace is not None and next_nonspace.ttype is T.Punctuation
            followed_by_paren = is_paren and next_nonspace.value == "("
            yield tok.value, followed_by_paren


def _next_meaningful(tokens: list, idx: int):
    for tok in tokens[idx + 1 :]:
        if tok.ttype in (T.Whitespace, T.Newline) or tok.is_whitespace:
            continue
        return tok
    return None


def _from_target_entries(tok):
    """A FROM/JOIN target is, per sqlparse, either a single Identifier
    (one table, optionally aliased -- `payments`, `payments p`,
    `payments AS p`), an IdentifierList (old-style comma-joined tables --
    `payments, q_totals`), or -- rarely -- a bare Name token. Yields
    (real_name, alias_or_None) for each table referenced."""
    if tok is None:
        return
    if isinstance(tok, IdentifierList):
        for ident in tok.get_identifiers():
            if isinstance(ident, Identifier):
                yield ident.get_real_name(), ident.get_alias()
    elif isinstance(tok, Identifier):
        yield tok.get_real_name(), tok.get_alias()
    elif tok.ttype in (T.Name,):
        yield tok.value, None


def _cte_name_entries(tok):
    """A WITH-clause target is a single Identifier (`recent AS (...)`) or
    an IdentifierList of several (`recent AS (...), older AS (...)`).
    Yields each CTE's name."""
    if tok is None:
        return
    if isinstance(tok, IdentifierList):
        for ident in tok.get_identifiers():
            if isinstance(ident, Identifier):
                name = ident.get_real_name()
                if name:
                    yield name
    elif isinstance(tok, Identifier):
        name = tok.get_real_name()
        if name:
            yield name


def _collect_tables_aliases_ctes(
    statement: sqlparse.sql.Statement,
) -> tuple[set[str], set[str], set[str]]:
    """Recursively walks the statement's structured token tree (not the
    flat token stream -- FROM/JOIN targets and WITH-clause CTE
    definitions are only recognizable as sqlparse's own Identifier /
    IdentifierList groupings) so subqueries, CTE bodies, and comma-joined
    FROM lists are all seen. Returns (table_names, aliases, cte_names)."""
    tables: set[str] = set()
    aliases: set[str] = set()
    cte_names: set[str] = set()

    def walk(token_list) -> None:
        toks = list(token_list)
        for i, tok in enumerate(toks):
            if tok.ttype is T.Keyword.CTE:
                cte_names.update(_cte_name_entries(_next_meaningful(toks, i)))
            elif tok.ttype is T.Keyword and (
                tok.value.upper() == "FROM" or "JOIN" in tok.value.upper()
            ):
                for name, alias in _from_target_entries(_next_meaningful(toks, i)):
                    if name:
                        tables.add(name)
                    if alias:
                        aliases.add(alias)
            if tok.is_group:
                walk(tok.tokens)

    walk(statement.tokens)
    return tables, aliases, cte_names


def _extract_column_aliases(statement: sqlparse.sql.Statement) -> set[str]:
    """Finds every `AS <alias>` (column aliases) anywhere in the
    statement, so ORDER BY/GROUP BY referencing that alias isn't flagged
    as an unknown column."""
    tokens = list(statement.flatten())
    aliases: set[str] = set()
    for i, tok in enumerate(tokens):
        if tok.ttype is T.Keyword and tok.value.upper() == "AS":
            j = i + 1
            while j < len(tokens) and tokens[j].ttype in (T.Whitespace, T.Newline):
                j += 1
            if j < len(tokens) and tokens[j].ttype in (T.Name,):
                aliases.add(tokens[j].value)
    return aliases


def validate_and_normalize(sql: str) -> str:
    """Returns a normalized, safe SQL string (LIMIT added if missing) or
    raises QueryRejected with a plain-English reason (section 4.2's
    validator steps)."""
    if not sql or not sql.strip():
        raise QueryRejected("empty query")

    stripped = sql.strip()
    # Exactly one optional trailing semicolon is fine; anything else
    # containing ';' is a multi-statement chain.
    body = stripped[:-1].strip() if stripped.endswith(";") else stripped
    if ";" in body:
        raise QueryRejected("multiple statements are not allowed (found ';' inside the query)")

    parsed = [s for s in sqlparse.parse(body) if s.token_first(skip_cm=True) is not None]
    if len(parsed) != 1:
        raise QueryRejected(f"expected exactly one SQL statement, found {len(parsed)}")
    statement = parsed[0]

    stmt_type = statement.get_type()
    if stmt_type != "SELECT":
        raise QueryRejected(f"only SELECT statements are allowed, got {stmt_type or 'UNKNOWN'}")

    upper_body = f" {body.upper()} "
    for banned in (
        "INSERT ", "UPDATE ", "DELETE ", "DROP ", "ALTER ", "TRUNCATE ", "GRANT ",
        "REVOKE ", "CREATE ", "CALL ", "MERGE ", "EXECUTE ", " INTO ", "--", "/*",
    ):
        if banned in upper_body:
            raise QueryRejected(f"disallowed keyword or comment found: {banned.strip()!r}")

    tables, table_aliases, cte_names = _collect_tables_aliases_ctes(statement)
    cte_names_lower = {c.lower() for c in cte_names}
    for table in tables:
        if table.lower() not in ALLOWED_TABLES and table.lower() not in cte_names_lower:
            raise QueryRejected(f"table/view {table!r} is not allow-listed")

    column_aliases = _extract_column_aliases(statement)
    allowed_names = (
        ALL_ALLOWED_COLUMNS
        | {t.lower() for t in table_aliases}
        | {a.lower() for a in column_aliases}
        | cte_names_lower
    )

    for name, followed_by_paren in _flatten_names(statement):
        lowered = name.lower().strip('"')
        if followed_by_paren:
            # FILTER (WHERE ...) is Postgres's aggregate-filter clause
            # modifier (e.g. SUM(x) FILTER (WHERE y = 'z')), not a function
            # call -- sqlparse tokenizes it as a plain Name since it isn't
            # one of its known keywords, so it would otherwise need to be
            # in _ALLOWED_FUNCTIONS like a real function. Its parenthesized
            # body is still scanned like any other subexpression (the
            # column/keyword checks below and in _collect_tables_aliases_ctes
            # aren't skipped), so this only exempts the word "filter" itself.
            if lowered == "filter":
                continue
            if lowered not in _ALLOWED_FUNCTIONS:
                raise QueryRejected(f"function {name!r} is not allow-listed")
            continue
        if lowered in {t.lower() for t in tables}:
            continue
        if lowered not in allowed_names:
            raise QueryRejected(f"column {name!r} is not allow-listed")

    has_limit = any(
        tok.ttype is T.Keyword and tok.value.upper() == "LIMIT" for tok in statement.flatten()
    )
    if not has_limit:
        body = f"{body} LIMIT {ROW_CAP}"

    return body


def _default_generate_sql(question: str, hint: str | None) -> str:
    """Real (non-test) path: calls the configured model provider. Never
    exercised by the guardrail tests (section 10: 'no secrets for
    tests') -- they inject their own generate_sql."""
    from area.providers import get_provider

    adapter = os.environ.get("AREA_ADAPTER")
    if not adapter:
        raise RuntimeError(
            "AREA_ADAPTER is not set -- cannot generate SQL without a configured model"
        )
    model_id = os.environ.get("AREA_QUERY_MODEL_ID")
    if not model_id:
        raise RuntimeError("AREA_QUERY_MODEL_ID is not set")
    provider = get_provider(adapter)
    prompt = (
        f"{SCHEMA_TEXT}\n\nQuestion: {question}\n"
        + (f"Hint: {hint}\n" if hint else "")
        + "\nRespond with ONLY the SQL, no explanation, no markdown fences."
    )
    result = provider.call(model_id=model_id, prompt=prompt, max_tokens=512, temperature=0.0)
    if result.error:
        raise RuntimeError(f"model call failed: {result.error}")
    return result.text.strip()


def _lazy_psycopg():
    import psycopg  # noqa: PLC0415 -- lazy, same pattern as area.load_neon

    return psycopg


def run(
    args: dict,
    *,
    generate_sql: Callable[[str, str | None], str] | None = None,
    database_url: str | None = None,
) -> ToolResult:
    question = args.get("question", "")
    hint = args.get("hint")
    generate_sql = generate_sql or _default_generate_sql

    try:
        raw_sql = generate_sql(question, hint)
    except Exception as exc:
        return ToolResult(ok=False, data=None, evidence=[], error=f"could not generate SQL: {exc}")

    try:
        sql = validate_and_normalize(raw_sql)
    except QueryRejected as exc:
        return ToolResult(ok=False, data=None, evidence=[], error=f"query rejected: {exc.reason}")

    database_url = (
        database_url
        or os.environ.get("AREA_READER_DATABASE_URL")
        or os.environ.get("DATABASE_URL")
    )
    if not database_url:
        return ToolResult(
            ok=False, data=None, evidence=[],
            error="no database configured (set AREA_READER_DATABASE_URL or DATABASE_URL) -- "
            "query was validated but not executed",
        )

    evidence_id = f"q_{hashlib.sha256(sql.encode()).hexdigest()[:8]}"
    psycopg = _lazy_psycopg()
    start = time.perf_counter()
    try:
        with psycopg.connect(database_url) as conn:
            with conn.cursor() as cur:
                cur.execute(sql)
                columns = [d.name for d in cur.description] if cur.description else []
                rows = [dict(zip(columns, row)) for row in cur.fetchall()]
    except Exception as exc:
        return ToolResult(ok=False, data=None, evidence=[], error=f"query execution failed: {exc}")
    execution_ms = (time.perf_counter() - start) * 1000

    data = {"sql": sql, "row_count": len(rows), "execution_ms": execution_ms, "rows": rows}
    evidence = [
        Evidence(
            evidence_id=evidence_id,
            kind="sql_rows",
            payload=rows,
            provenance={"sql": sql, "row_count": len(rows), "execution_ms": execution_ms},
        )
    ]
    return ToolResult(ok=True, data=data, evidence=evidence, error=None)


TOOL = Tool(
    name="query_tool",
    description=(
        "Answers a question by writing one SELECT against the allow-listed payments "
        "schema (payments, q_totals, q_by_specialty, q_by_state), validating it "
        "(single SELECT, allow-listed tables/columns, LIMIT enforced), and running it "
        "read-only. Returns the rows and a citable evidence_id."
    ),
    input_schema={
        "type": "object",
        "required": ["question"],
        "properties": {
            "question": {"type": "string"},
            "hint": {"type": ["string", "null"]},
        },
    },
    fn=run,
)
