"""Tests for area.tools.citation_checker (SPEC-area.md section 4.4).
Named test cases from the spec: cited number passes; rounded number
passes; derived percentage passes with a derivation; a number with no
source fails; a wrong derivation fails."""

from __future__ import annotations

from area.tools import Evidence
from area.tools.citation_checker import Derivation, check, run


def test_cited_number_passes():
    evidence = [Evidence("q_ab12cd34", "sql_rows", [{"total_usd": 1234.56}])]
    result = check("Total payments were $1,234.56 [q_ab12cd34].", evidence)
    assert result.accepted is True
    assert result.numbers[0].passed is True
    assert result.unverified == []


def test_rounded_number_passes():
    # Evidence carries more precision than the answer displays; section
    # 4.4's "displayed precision" tolerance branch.
    evidence = [Evidence("q_ab12cd34", "sql_rows", [{"total_usd": 1234.5678}])]
    result = check("Total payments were $1,234.57 [q_ab12cd34].", evidence)
    assert result.accepted is True
    assert result.numbers[0].passed is True


def test_derived_percentage_passes_with_derivation():
    derivations = [Derivation("d_share1", "1234.56 / 5000 * 100", 24.6912)]
    result = check("Food and Beverage was 24.69% [d_share1] of matched dollars.", [], derivations)
    assert result.accepted is True
    assert result.numbers[0].passed is True
    assert result.numbers[0].cited_id == "d_share1"


def test_number_with_no_source_fails():
    result = check("Total payments were $9,999.00 with no citation at all.", [])
    assert result.accepted is False
    assert "$9,999.00" in result.unverified
    assert result.numbers[0].reason == "no citation marker follows this number"


def test_number_citing_unknown_id_fails():
    evidence = [Evidence("q_real", "sql_rows", [{"total_usd": 100}])]
    result = check("Total was $100 [q_does_not_exist].", evidence)
    assert result.accepted is False
    assert "matches no evidence or derivation" in result.numbers[0].reason


def test_wrong_derivation_fails():
    # The composer claims 1234.56 / 5000 * 100 == 30.0, but it actually
    # recomputes to ~24.69 -- a wrong derivation, per spec's own named
    # test case.
    derivations = [Derivation("d_bad", "1234.56 / 5000 * 100", 30.0)]
    result = check("Food and Beverage was 30.0% [d_bad] of matched dollars.", [], derivations)
    assert result.accepted is False
    assert "recomputes to" in result.numbers[0].reason


def test_years_are_excluded_from_the_check():
    evidence = [Evidence("q_x", "sql_rows", [{"total_usd": 500}])]
    # 2023 has no marker at all -- if it were checked, this would fail;
    # since it's a year, it must simply not appear in the verdict list.
    result = check("In 2023, total payments were $500 [q_x].", evidence)
    assert result.accepted is True
    assert len(result.numbers) == 1
    assert result.numbers[0].raw_text == "$500"


def test_multiple_numbers_mixed_verdicts():
    evidence = [Evidence("q_1", "sql_rows", [{"total_usd": 100}])]
    text = "Total was $100 [q_1], but the other figure $200 has no source."
    result = check(text, evidence)
    assert result.accepted is False
    assert len(result.numbers) == 2
    assert result.numbers[0].passed is True
    assert result.numbers[1].passed is False


def test_percent_sign_number_matches_evidence_with_percent():
    evidence = [Evidence("q_p", "sql_rows", [{"share_pct": 5.2}])]
    result = check("The share was 5.2% [q_p].", evidence)
    assert result.accepted is True


def test_run_tool_entry_point_matches_check():
    args = {
        "answer_text": "Total was $100 [q_1].",
        "evidence": [{"evidence_id": "q_1", "kind": "sql_rows", "payload": [{"total_usd": 100}]}],
    }
    result = run(args)
    assert result.ok is True
    assert result.data["accepted"] is True
    assert result.data["unverified"] == []


def test_run_tool_entry_point_never_raises_on_bad_input():
    result = run({"answer_text": "$5 [q_1]", "evidence": [{"evidence_id": "q_1"}]})
    assert result.ok is True
    assert result.data["accepted"] is False


def test_glp_1_product_name_suffix_is_not_treated_as_an_uncited_number():
    # Found for real running area evals (task W-B4, CONTEXT.md D19) against
    # a live Bedrock composer answer that mentioned "GLP-1" with no other
    # digits in it -- the old bare_int alternative had no left boundary, so
    # it matched the "1" glued onto "GLP-" and flagged it as an uncited
    # claim.
    result = check("No matching data is available yet for GLP-1 records.", [])
    assert result.accepted is True
    assert result.numbers == []


def test_number_immediately_after_a_word_is_still_caught_when_uncited():
    # The GLP-1 guard must not blanket-exempt every letter-adjacent digit --
    # a real uncited number in that shape should still fail.
    result = check("Model v2 reported total 42 with no citation.", [])
    assert result.accepted is False
    assert any(n.raw_text == "42" for n in result.numbers)
