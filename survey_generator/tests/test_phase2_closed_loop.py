"""Closed-loop architecture: the deterministic validator drives revision.

Covers the pieces the mocked full-pipeline test doesn't exercise with real issues:
the validator → Issue conversion, the holistic Survey Editor directive parsing, and
the deterministic sanitize net that guarantees the gate can always be cleared.
"""
from __future__ import annotations

from survey_generator.phase2 import pipeline
from survey_generator.phase2.models import SectionProposal, SurveyQuestion, ValidationReport
from survey_generator.phase2.validator import validate


def test_validator_issues_converts_blockers_and_warnings_skips_survey_level():
    report = ValidationReport()
    report.issue_rows = [
        {"severity": "blocker", "issue_type": "missing_options", "q_id": "B_Q12",
         "message": "needs 2 options", "recommended_fix": "add options"},
        {"severity": "warning", "issue_type": "construct_type_mismatch", "q_id": "C_Q05",
         "message": "type odd", "recommended_fix": "confirm type"},
        {"severity": "burden", "issue_type": "respondent_burden", "q_id": "",
         "message": "LOI 67 min", "recommended_fix": "shorten"},  # no q_id → advisory, skipped
    ]
    issues = pipeline._validator_issues(report, include_warnings=True)
    assert len(issues) == 2
    assert {i.severity for i in issues} == {"critical", "minor"}          # blocker→critical, warning→minor
    assert all(i.fix_recommendation for i in issues)                       # the rule's fix rides along
    assert all(i.source_agent == "deterministic_validator" for i in issues)
    # blockers-only mode leaves warnings out
    assert len(pipeline._validator_issues(report, include_warnings=False)) == 1


def test_editor_directives_parse_to_issues():
    data = {
        "summary": "redundant channel questions across C and F",
        "directives": [
            {"action": "delete", "q_id": "F_Q06", "section_id": "F", "instruction": "redundant with C_Q05"},
            {"action": "revise", "q_id": "C_Q05", "section_id": "C", "instruction": "standardize to RATING_7_IMPORTANCE"},
            {"action": "revise", "q_id": "", "instruction": "no q_id — must be dropped"},
        ],
    }
    issues, summary = pipeline._parse_editor_directives(data)
    assert summary
    assert len(issues) == 2                                                 # blank q_id dropped
    delete = next(i for i in issues if i.q_id == "F_Q06")
    assert delete.issue_type == "editorial_delete"
    assert "deleted_question_ids" in delete.fix_recommendation             # routed to a delete for the reviser


def test_sanitize_net_clears_residual_blockers():
    sections = [SectionProposal(section_id="B", section_title="B")]
    qs = [
        SurveyQuestion(q_id="B_Q01", section="B", section_id="B",
                       question_text="How likely to prescribe X?",
                       question_type="RATING_7_LIKELIHOOD", options=[]),          # blocker: no rows
        SurveyQuestion(q_id="B_Q02", section="B", section_id="B",
                       question_text="Pick one", question_type="SINGLE_SELECT",
                       options=["[1|a]", "[2|b]"],
                       routing={"condition": "x", "next": "J_040 for opt 3"}),     # blocker: dangling route
        SurveyQuestion(q_id="B_Q03", section="B", section_id="B",
                       question_text="screen?", question_type="SINGLE_SELECT",
                       options=["[1|a]", "[2|b]"],
                       routing={"condition": "x", "next": "TERMINATE"}),           # valid terminal — keep
    ]
    assert len(validate(qs, [], [], sections).blockers) == 2

    pipeline._sanitize_structural(qs, sections)

    assert validate(qs, [], [], sections).blockers == []                   # gate is clearable
    assert qs[0].options == ["[1 | How likely to prescribe X?]"]           # row synthesised from the stem
    assert qs[1].routing.next == ""                                        # dangling target cleared
    assert any("ROUTING" in p for p in qs[1].programming_instructions)     # ...but preserved as a note
    assert qs[2].routing.next == "TERMINATE"                               # terminal route untouched
