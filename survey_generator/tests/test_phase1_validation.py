from __future__ import annotations

from survey_generator.phase1.validation import approved_hypotheses, prepare_hypothesis_rows


def test_hypothesis_validation_duplicates_statuses_and_approval_filter():
    rows, issues = prepare_hypothesis_rows([
        {"hypothesis_id": "H1", "text": "Adoption is higher among physicians who perceive stronger efficacy", "category": "Barriers"},
        {"hypothesis_id": "H1", "text": "Adoption is higher among physicians who perceive stronger efficacy", "category": "Barriers"},
        {"hypothesis_id": "H3", "text": "Too short", "category": "Invalid", "status": "Rejected"},
    ], selected_frameworks=["Barriers"], study_type="ATU", default_audience="HCP")
    assert len(rows) == 3
    assert any(i["field"] == "text" and "duplicate" in i["message"].lower() for i in issues)
    assert any(i["field"] == "category" for i in issues)
    approved = approved_hypotheses(rows)
    assert all(r["status"] == "Approved" for r in approved)
    assert all(r["text"] for r in approved)
