from __future__ import annotations

from survey_generator.phase2.models import Hypothesis, SectionProposal, StudySetup
from survey_generator.phase2.pipeline import run_pipeline_after_gate, run_section_suggester


class FakeClient:
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


def test_mocked_full_phase2_pipeline_without_real_llm():
    study = StudySetup(study_type="ATU", target_population="HCPs", client_name="Client", study_title="Study")
    hyps = [Hypothesis(hypothesis_id="H001", text="Access is associated with higher adoption", category="Drivers & barriers")]
    sections_json = [{"section_id": "A", "section_title": "Drivers", "rationale": "Test", "is_must_have": True}]
    client1 = FakeClient([sections_json])
    sections = run_section_suggester(client1, study, hyps)
    assert sections[0].section_id == "A"

    responses = [
        [{"hypothesis_id": "H001", "section_id": "A", "section_title": "Drivers", "construct": "attitude", "question_type": "SINGLE_SELECT", "priority": "primary"}],
        [{"hypothesis_id": "H001", "section_id": "A", "section_title": "Drivers", "construct": "attitude", "question_type": "SINGLE_SELECT", "priority": "primary"}],
        [{
            "q_id": "Q1", "section": "Drivers", "section_id": "A", "section_title": "Drivers",
            "hypothesis_ids": ["H001"], "question_text": "How simple is access?", "question_type": "SINGLE_SELECT",
            "options": ["[1 | Simple]", "[2 | Not simple]"],
        }],
        [], [], [],
        {"revision_list": [], "escalated_issues": [], "auto_fix_issues": [], "major_issue_count": 0},
    ]
    client2 = FakeClient(responses)
    result = run_pipeline_after_gate(client2, study, hyps, sections)
    assert result["questions"]
    assert result["validation"].status in {"PASS", "WARNING"}
    assert result["questions"][0].q_id == "A_Q01"
