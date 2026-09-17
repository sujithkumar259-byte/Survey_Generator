from __future__ import annotations

from survey_generator.phase1.csv_contract import parse_hypothesis_csv_response
from survey_generator.phase1.frameworks import CUSTOM_FRAMEWORK_NAME, category_suggestions, selected_framework_prompt_block
from survey_generator.phase1.models import StudyBrief
from survey_generator.phase1.pipeline import run_generator


class CSVClient:
    max_json_retries = 0

    def __init__(self, text):
        self.text = text

    def complete_text(self, _system, _user):
        return self.text


def test_framework_prompt_block_and_category_suggestions():
    selected = ["HCP · PACE-B Framework"]
    block = selected_framework_prompt_block(selected)
    assert "PACE-B" in block
    assert "Potential" in block
    assert "Category guidance" in block
    assert "Potential" in category_suggestions(selected)


def test_custom_framework_is_included_in_prompt_block():
    custom = "My framework covers Motivation, Access friction, and Support needs."
    block = selected_framework_prompt_block([CUSTOM_FRAMEWORK_NAME], custom)
    assert "Custom framework" in block
    assert "Access friction" in block


def test_phase1_generator_accepts_construct_category_not_framework_name():
    csv_text = (
        "hypotheses number,category,Hypotheses,rationale\n"
        "H001,Potential,HCPs who treat more eligible patients are more likely to be receptive to adoption,Tests opportunity-based prioritization.\n"
        "H002,Environment,HCPs who expect payer restrictions are less likely to prescribe routinely,Tests whether access barriers suppress behavior.\n"
    )
    brief = StudyBrief(
        study_type="Segmentation",
        target_audience="HCPs",
        frameworks=["HCP · PACE-B Framework"],
    )
    hyps = run_generator(CSVClient(csv_text), brief, [], [])
    assert [h.category for h in hyps] == ["Potential", "Environment"]
    assert all("who" in h.text.lower() for h in hyps)


def test_csv_parser_does_not_require_category_to_equal_framework():
    parsed = parse_hypothesis_csv_response(
        "hypotheses number,category,Hypotheses,rationale\n"
        "H001,Peer validation,HCPs who rely on peer experience are more likely to be receptive to colleague-led evidence,Tests engagement strategy.",
        allowed_categories=["Potential", "Attitudes", "Peer validation"],
    )
    assert parsed.rows
    assert parsed.rows[0]["category"] == "Peer validation"
