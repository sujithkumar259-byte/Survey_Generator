from __future__ import annotations

from survey_generator.phase2.models import Hypothesis, SectionProposal, StudySetup
from survey_generator.phase2.pipeline import _coerce_json_list, run_pipeline_after_gate, run_section_suggester


class WrapperClient:
    max_json_retries = 0

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0

    def complete_json(self, _system, _user, expected_type=None):
        self.calls += 1
        if not self.responses:
            raise AssertionError("No fake LLM responses remaining")
        return self.responses.pop(0)

    def usage_snapshot(self):
        return {"calls": self.calls, "estimated_total_tokens": 0}


def test_coerce_json_list_recovers_common_wrappers():
    assert _coerce_json_list({"sections": [{"section_id": "A", "section_title": "Drivers"}]}, SectionProposal)[0]["section_id"] == "A"
    assert _coerce_json_list({"data": [{"section_id": "B", "section_title": "Barriers"}]}, SectionProposal)[0]["section_id"] == "B"
    assert _coerce_json_list({"section_id": "C", "section_title": "Profile"}, SectionProposal)[0]["section_id"] == "C"


def test_phase2_pipeline_accepts_dict_wrapped_model_outputs():
    study = StudySetup(study_type="Segmentation", target_population="HCPs", client_name="Client", study_title="Study")
    hyps = [
        Hypothesis(
            hypothesis_id="H001",
            text="HCPs who perceive strong evidence are more likely to be receptive to adoption.",
            category="Evidence preference",
        )
    ]

    sections = run_section_suggester(
        WrapperClient([
            {"sections": [{"section_id": "A", "section_title": "Evidence and adoption", "rationale": "Measures evidence-led receptivity", "is_must_have": True}]}
        ]),
        study,
        hyps,
    )
    assert sections[0].section_id == "A"

    responses = [
        {"mappings": [{"hypothesis_id": "H001", "section_id": "A", "section_title": "Evidence and adoption", "section": "Evidence and adoption", "construct": "likelihood", "question_type": "RATING_7_LIKELIHOOD", "priority": "primary"}]},
        {"hypothesis_mappings": [{"hypothesis_id": "H001", "section_id": "A", "section_title": "Evidence and adoption", "section": "Evidence and adoption", "construct": "likelihood", "question_type": "RATING_7_LIKELIHOOD", "priority": "primary"}]},
        {"questions": [{
            "q_id": "A_010",
            "section": "Evidence and adoption",
            "section_id": "A",
            "section_title": "Evidence and adoption",
            "hypothesis_ids": ["H001"],
            "question_text": "How likely are you to consider the product if evidence is credible?",
            "question_type": "RATING_7_LIKELIHOOD",
            "options": ["[1 | Credible evidence]"],
            "scale": {"min": 1, "max": 7, "anchor_low": "Not at all likely", "anchor_high": "Extremely likely"},
        }]},
        {"issues": []},
        {"issues": []},
        {"issues": []},
        {"revision_list": [], "escalated_issues": [], "auto_fix_issues": [], "major_issue_count": 0},
    ]
    result = run_pipeline_after_gate(WrapperClient(responses), study, hyps, sections)
    assert result["questions"][0].q_id == "A_Q01"
    assert result["validation"].status in {"PASS", "WARNING"}
