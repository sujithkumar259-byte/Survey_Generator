"""Phase 2 review/edit/export helpers for the Streamlit UI."""
from __future__ import annotations

from typing import Iterable, List

from .models import (
    ConsolidatedResult,
    HMapping,
    Hypothesis,
    RoutingSpec,
    ScaleSpec,
    SurveyQuestion,
    ValidationReport,
)

QUESTION_EDITOR_COLUMNS = [
    "section_id", "section_title", "q_id", "question_type", "question_text",
    "options", "grid_columns", "dropdown_options",
    "scale_min", "scale_max", "scale_anchor_low", "scale_anchor_high",
    "routing_condition", "routing_next", "termination_logic", "option_level_logic",
    "programming_instructions", "section_objective", "source_hypotheses",
    "interviewer_notes", "notes", "flag_status", "flag_description",
]


def _list_to_cell(values: Iterable[str] | None) -> str:
    return "\n".join(str(v) for v in (values or []) if str(v).strip())


def _cell_to_list(value) -> list[str]:
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    text = str(value or "").replace("\r\n", "\n").replace("\r", "\n")
    return [ln.strip() for ln in text.split("\n") if ln.strip()]


def _parse_hypothesis_ids(value) -> list[str]:
    text = str(value or "").replace(",", ";").replace("\n", ";")
    return [x.strip() for x in text.split(";") if x.strip()]


def _parse_int(value, default: int) -> int:
    try:
        return int(float(str(value).strip()))
    except Exception:  # noqa: BLE001
        return default


def question_to_editor_row(q: SurveyQuestion) -> dict:
    return {
        "section_id": q.section_id,
        "section_title": q.section_title or q.section,
        "q_id": q.q_id,
        "question_type": q.question_type,
        "question_text": q.question_text,
        "options": _list_to_cell(q.options),
        "grid_columns": _list_to_cell(q.grid_columns),
        "dropdown_options": _list_to_cell(q.dropdown_options),
        "scale_min": q.scale.min if q.scale else "",
        "scale_max": q.scale.max if q.scale else "",
        "scale_anchor_low": q.scale.anchor_low if q.scale else "",
        "scale_anchor_high": q.scale.anchor_high if q.scale else "",
        "routing_condition": q.routing.condition if q.routing else "",
        "routing_next": q.routing.next if q.routing else "",
        "termination_logic": _list_to_cell(q.termination_logic),
        "option_level_logic": _list_to_cell(q.option_level_logic),
        "programming_instructions": _list_to_cell(q.programming_instructions),
        "section_objective": q.section_objective,
        "source_hypotheses": "; ".join(q.hypothesis_ids),
        "interviewer_notes": q.interviewer_notes,
        "notes": q.notes,
        "flag_status": q.flag_status,
        "flag_description": q.flag_description,
    }


def questions_to_editor_rows(questions: List[SurveyQuestion]) -> list[dict]:
    return [question_to_editor_row(q) for q in questions]


def editor_rows_to_questions(rows) -> list[SurveyQuestion]:
    if hasattr(rows, "to_dict"):
        rows = rows.to_dict("records")
    out: list[SurveyQuestion] = []
    for row in rows or []:
        if not str(row.get("question_text", "")).strip() and not str(row.get("q_id", "")).strip():
            continue
        scale = None
        has_scale = any(str(row.get(k, "")).strip() for k in ("scale_min", "scale_max", "scale_anchor_low", "scale_anchor_high"))
        if has_scale:
            scale = ScaleSpec(
                min=_parse_int(row.get("scale_min"), 1),
                max=_parse_int(row.get("scale_max"), 7),
                anchor_low=str(row.get("scale_anchor_low", "") or ""),
                anchor_high=str(row.get("scale_anchor_high", "") or ""),
            )
        routing = None
        if str(row.get("routing_condition", "")).strip() or str(row.get("routing_next", "")).strip():
            routing = RoutingSpec(
                condition=str(row.get("routing_condition", "") or "all respondents"),
                next=str(row.get("routing_next", "") or ""),
            )
        section_title = str(row.get("section_title", "") or "")
        out.append(SurveyQuestion(
            q_id=str(row.get("q_id", "") or "").strip(),
            section=section_title,
            section_id=str(row.get("section_id", "") or "").strip(),
            section_title=section_title,
            hypothesis_ids=_parse_hypothesis_ids(row.get("source_hypotheses", "")),
            question_text=str(row.get("question_text", "") or ""),
            question_type=str(row.get("question_type", "") or "OPEN_END"),
            options=_cell_to_list(row.get("options", "")),
            grid_columns=_cell_to_list(row.get("grid_columns", "")),
            dropdown_options=_cell_to_list(row.get("dropdown_options", "")),
            scale=scale,
            routing=routing,
            termination_logic=_cell_to_list(row.get("termination_logic", "")),
            option_level_logic=_cell_to_list(row.get("option_level_logic", "")),
            programming_instructions=_cell_to_list(row.get("programming_instructions", "")),
            section_objective=str(row.get("section_objective", "") or ""),
            interviewer_notes=str(row.get("interviewer_notes", "") or ""),
            notes=str(row.get("notes", "") or ""),
            flag_status=str(row.get("flag_status", "clean") or "clean"),
            flag_description=str(row.get("flag_description", "") or ""),
        ))
    return out


def validation_policy(report: ValidationReport, override: bool = False, approved: bool = False) -> dict:
    has_blockers = bool(report.blockers)
    validation_allows_export = (not has_blockers) or bool(override)
    return {
        "status": "BLOCKER" if has_blockers else ("WARNING" if report.warnings else "PASS"),
        "can_export": bool(approved) and validation_allows_export,
        "validation_allows_export": validation_allows_export,
        "requires_override": has_blockers,
        "requires_approval": not bool(approved),
        "override_used": bool(override and has_blockers),
        "approved": bool(approved),
    }


def coverage_matrix_rows(hypotheses: List[Hypothesis], report: ValidationReport) -> list[dict]:
    rows: list[dict] = []
    for h in hypotheses:
        qs = report.coverage_map.get(h.hypothesis_id, []) or []
        rows.append({
            "Hypothesis_ID": h.hypothesis_id,
            "Hypothesis": h.text,
            "Covered_By": "; ".join(qs),
            "Coverage_Status": "Covered" if qs else "Not Covered",
            "Category": h.category,
            "Priority": h.priority,
        })
    return rows


def issue_dashboard_rows(report: ValidationReport, consolidated: ConsolidatedResult | None = None) -> list[dict]:
    rows = [dict(r) for r in report.issue_rows]
    if consolidated:
        for bucket, issues in [
            ("Revision", consolidated.revision_list),
            ("Escalated", consolidated.escalated_issues),
            ("Auto-fix", consolidated.auto_fix_issues),
        ]:
            for i in issues:
                rows.append({
                    "severity": i.severity,
                    "issue_type": i.issue_type,
                    "q_id": i.q_id,
                    "message": i.description,
                    "recommended_fix": i.fix_recommendation,
                    "status": bucket,
                })
    # stable de-dupe
    seen, out = set(), []
    for row in rows:
        key = (row.get("severity"), row.get("issue_type"), row.get("q_id"), row.get("message"))
        if key not in seen:
            seen.add(key)
            out.append(row)
    return out


def question_diffs(before: List[SurveyQuestion], after: List[SurveyQuestion]) -> list[dict]:
    before_by_id = {q.q_id: q for q in before}
    after_by_id = {q.q_id: q for q in after}
    fields = [
        "section_id", "section_title", "question_text", "question_type", "options",
        "grid_columns", "dropdown_options", "hypothesis_ids", "programming_instructions",
        "termination_logic", "section_objective", "flag_status", "flag_description",
    ]
    rows: list[dict] = []
    for qid, q_after in after_by_id.items():
        q_before = before_by_id.get(qid)
        if q_before is None:
            rows.append({"q_id": qid, "change_type": "inserted", "field": "question", "before": "", "after": q_after.question_text})
            continue
        for field in fields:
            b = getattr(q_before, field, "")
            a = getattr(q_after, field, "")
            if b != a:
                rows.append({"q_id": qid, "change_type": "modified", "field": field, "before": str(b), "after": str(a)})
    for qid, q_before in before_by_id.items():
        if qid not in after_by_id:
            rows.append({"q_id": qid, "change_type": "deleted", "field": "question", "before": q_before.question_text, "after": ""})
    return rows
