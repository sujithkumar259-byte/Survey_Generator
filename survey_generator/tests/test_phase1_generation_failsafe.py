from __future__ import annotations

import ast
from pathlib import Path

import pytest

from survey_generator.llm.client import LLMError
from survey_generator.phase1.models import SourceDoc, StudyBrief
from survey_generator.phase1.pipeline import run_generator, starter_hypotheses_from_context


class DummyClient:
    def __init__(self, payload):
        self.payload = payload

    def complete_json(self, *_args, **_kwargs):
        return self.payload


def test_run_generator_accepts_framework_keyed_model_output():
    brief = StudyBrief(
        study_type="Segmentation",
        target_audience="HCPs",
        key_decisions="Which segments to target",
        frameworks=["Drivers & barriers"],
    )
    docs = [SourceDoc(filename="deck.pptx", kind="pptx", text="adoption barriers and long-term efficacy")]
    payload = {
        "Drivers & barriers": [
            {
                "hypothesis": "HCP adoption is higher when long-term outcomes evidence is perceived as credible.",
                "rationale": "Distinguishes evidence-led adoption segments.",
            }
        ]
    }
    hyps = run_generator(DummyClient(payload), brief, docs)
    assert len(hyps) == 1
    assert hyps[0].text.startswith("HCP adoption is higher")
    assert hyps[0].category == "Drivers & barriers"


def test_run_generator_reports_unrecognized_shape_cleanly():
    brief = StudyBrief(study_type="Segmentation", frameworks=["Drivers & barriers"])
    with pytest.raises(LLMError, match="required CSV format"):
        run_generator(DummyClient({"metadata": {"count": 0}}), brief, [])


def test_starter_hypotheses_are_editable_not_auto_approved():
    brief = StudyBrief(
        study_type="Segmentation",
        target_audience="HCPs",
        key_decisions="Which segments to target",
        disease_area="ASCVD",
        frameworks=["Drivers & barriers"],
    )
    docs = [SourceDoc(filename="proposal.pptx", kind="pptx", text="source text")]
    rows = starter_hypotheses_from_context(brief, docs)
    assert rows
    assert all(r["review_status"] == "Needs Edit" for r in rows)
    assert all("Fallback" in "; ".join(r.get("validation_warnings", [])) for r in rows)



def test_run_generator_accepts_strict_csv_model_output():
    brief = StudyBrief(
        study_type="Segmentation",
        target_audience="HCPs",
        key_decisions="Which segments to target",
        frameworks=["Drivers & barriers"],
    )
    docs = [SourceDoc(filename="deck.pptx", kind="pptx", text="adoption barriers and long-term efficacy")]

    class CSVClient:
        max_json_retries = 0
        def complete_text(self, *_args, **_kwargs):
            return (
                "hypotheses number,category,Hypotheses,rationale\n"
                "H001,Drivers & barriers,HCP adoption is higher when long-term outcomes evidence is perceived as credible.,Distinguishes evidence-led adoption segments.\n"
            )

    hyps = run_generator(CSVClient(), brief, docs)
    assert len(hyps) == 1
    assert hyps[0].hypothesis_id == "H001"
    assert hyps[0].text.startswith("HCP adoption is higher")
    assert hyps[0].rationale.startswith("Distinguishes")


def test_run_generator_retries_malformed_csv_then_succeeds():
    brief = StudyBrief(study_type="Segmentation", frameworks=["Drivers & barriers"])

    class RetryClient:
        max_json_retries = 1
        def __init__(self):
            self.calls = 0
        def complete_text(self, *_args, **_kwargs):
            self.calls += 1
            if self.calls == 1:
                return "Here are some ideas, but not CSV"
            return (
                "hypotheses number,category,Hypotheses,rationale\n"
                "H001,Drivers & barriers,Adoption is higher when efficacy is valued.,Tests a driver for segmentation.\n"
            )

    client = RetryClient()
    hyps = run_generator(client, brief, [])
    assert len(hyps) == 1
    assert client.calls == 2

def test_app_streamlit_widgets_have_explicit_keys():
    app_py = Path(__file__).resolve().parents[1] / "app.py"
    tree = ast.parse(app_py.read_text(encoding="utf-8"))
    widget_names = {
        "text_input", "text_area", "selectbox", "multiselect", "button", "download_button",
        "file_uploader", "data_editor", "checkbox", "radio",
    }
    missing = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in widget_names:
            if not any(keyword.arg == "key" for keyword in node.keywords):
                missing.append((node.lineno, node.func.attr))
    assert not missing, f"Streamlit widgets missing explicit keys: {missing}"
