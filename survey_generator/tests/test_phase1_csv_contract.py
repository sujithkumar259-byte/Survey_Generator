from __future__ import annotations

from survey_generator.phase1.csv_contract import (
    canonical_header_line,
    csv_example_rows,
    parse_hypothesis_csv_response,
    rows_to_simple_hypothesis_csv,
)


def test_parse_exact_simple_hypothesis_csv():
    raw = (
        "hypotheses number,category,Hypotheses,rationale\n"
        'H001,"Drivers & barriers","Adoption is higher when evidence is credible.","Useful for segmentation."\n'
    )
    result = parse_hypothesis_csv_response(raw, allowed_categories=["Drivers & barriers"])
    assert result.ok
    assert result.detected_format == "csv"
    assert result.rows[0]["hypothesis_id"] == "H001"
    assert result.rows[0]["text"].startswith("Adoption is higher")


def test_parse_csv_with_preamble_and_markdown_fence():
    raw = "```csv\nhypotheses number,category,Hypotheses,rationale\n1,Drivers & barriers,Adoption is higher when access is easy,Tests access barriers\n```"
    result = parse_hypothesis_csv_response(raw, allowed_categories=["Drivers & barriers"])
    assert result.ok
    assert result.rows[0]["hypothesis_id"] == "H001"


def test_parse_recovers_markdown_table():
    raw = """
| hypotheses number | category | Hypotheses | rationale |
| --- | --- | --- | --- |
| H001 | Drivers & barriers | Adoption is higher when efficacy is trusted. | Tests driver strength. |
"""
    result = parse_hypothesis_csv_response(raw, allowed_categories=["Drivers & barriers"])
    assert result.ok
    assert result.detected_format == "markdown_table"


def test_simple_csv_export_contract():
    out = rows_to_simple_hypothesis_csv([
        {"hypothesis_id": "H001", "category": "Drivers & barriers", "text": "A comma, inside text is quoted", "rationale": "Reason"}
    ])
    assert out.startswith(canonical_header_line())
    assert '"A comma, inside text is quoted"' in out
    assert "hypotheses number,category,Hypotheses,rationale" in csv_example_rows(["Drivers & barriers"])
