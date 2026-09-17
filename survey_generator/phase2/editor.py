"""Phase 2 UI/data helpers for human review before workbook export."""
from __future__ import annotations

from typing import Any, Iterable, List

from pydantic import ValidationError

from .models import RoutingSpec, ScaleSpec, SurveyQuestion

QUESTION_EDITOR_COLUMNS = [
    "q_id", "section_id", "section_title", "display_order", "question_type",
    "question_text", "options", "grid_columns", "dropdown_options",
    "scale_min", "scale_max", "scale_anchor_low", "scale_anchor_high",
    "routing_condition", "routing_next", "termination_logic", "option_level_logic",
    "programming_instructions", "audience_split", "theme", "section_objective",
    "notes", "source_hypotheses", "flag_status", "flag_description",
]


def _join(values: Iterable[str] | str | None) -> str:
    if values is None:
        return ""
    if isinstance(values, str):
        return values
    return "\n".join(str(v) for v in values if str(v or "").strip())


def _split(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    text = str(value or "").replace("\r\n", "\n")
    if "\n" in text:
        return [ln.strip() for ln in text.split("\n") if ln.strip()]
    if ";" in text:
        return [ln.strip() for ln in text.split(";") if ln.strip()]
    return [text.strip()] if text.strip() else []


def _to_int(value: Any, default: int) -> int:
    try:
        if value is None or str(value).strip() == "":
            return default
        return int(float(str(value).strip()))
    except Exception:  # noqa: BLE001
        return default


def questions_to_editor_rows(questions: list[SurveyQuestion] | list[dict]) -> list[dict]:
    rows: list[dict] = []
    for raw in questions:
        q = raw if isinstance(raw, SurveyQuestion) else SurveyQuestion.model_validate(raw)
        rows.append({
            "q_id": q.q_id,
            "section_id": q.section_id,
            "section_title": q.section_title or q.section,
            "display_order": q.display_order or q.q_id,
            "question_type": q.question_type,
            "question_text": q.question_text,
            "options": _join(q.options),
            "grid_columns": _join(q.grid_columns),
            "dropdown_options": _join(q.dropdown_options),
            "scale_min": q.scale.min if q.scale else "",
            "scale_max": q.scale.max if q.scale else "",
            "scale_anchor_low": q.scale.anchor_low if q.scale else "",
            "scale_anchor_high": q.scale.anchor_high if q.scale else "",
            "routing_condition": q.routing.condition if q.routing else "",
            "routing_next": q.routing.next if q.routing else "",
            "termination_logic": _join(q.termination_logic),
            "option_level_logic": _join(q.option_level_logic),
            "programming_instructions": _join(q.programming_instructions),
            "audience_split": q.audience_split or "ALL",
            "theme": q.theme,
            "section_objective": q.section_objective,
            "notes": q.notes,
            "source_hypotheses": "; ".join(q.hypothesis_ids or []),
            "flag_status": q.flag_status,
            "flag_description": q.flag_description,
        })
    return rows


def questions_from_editor_rows(rows: Any) -> tuple[list[SurveyQuestion], list[dict]]:
    if hasattr(rows, "to_dict"):
        rows = rows.to_dict("records")
    questions: list[SurveyQuestion] = []
    errors: list[dict] = []
    for idx, row in enumerate(rows or [], start=1):
        if not any(str(row.get(c, "")).strip() for c in QUESTION_EDITOR_COLUMNS if c in row):
            continue
        scale = None
        if any(str(row.get(k, "")).strip() for k in ("scale_min", "scale_max", "scale_anchor_low", "scale_anchor_high")):
            scale = ScaleSpec(
                min=_to_int(row.get("scale_min"), 1),
                max=_to_int(row.get("scale_max"), 7),
                anchor_low=str(row.get("scale_anchor_low", "") or ""),
                anchor_high=str(row.get("scale_anchor_high", "") or ""),
            )
        routing = None
        if str(row.get("routing_condition", "")).strip() or str(row.get("routing_next", "")).strip():
            routing = RoutingSpec(
                condition=str(row.get("routing_condition", "") or "all respondents"),
                next=str(row.get("routing_next", "") or ""),
            )
        payload = {
            "q_id": str(row.get("q_id", "") or "").strip(),
            "section": str(row.get("section_title", row.get("section_id", "")) or ""),
            "section_id": str(row.get("section_id", "") or "").strip(),
            "section_title": str(row.get("section_title", "") or ""),
            "display_order": str(row.get("display_order", row.get("q_id", "")) or ""),
            "hypothesis_ids": _split(row.get("source_hypotheses", "")),
            "question_text": str(row.get("question_text", "") or ""),
            "question_type": str(row.get("question_type", "") or "").strip().upper(),
            "options": _split(row.get("options", "")),
            "grid_columns": _split(row.get("grid_columns", "")),
            "dropdown_options": _split(row.get("dropdown_options", "")),
            "scale": scale,
            "routing": routing,
            "theme": str(row.get("theme", "") or ""),
            "audience_split": str(row.get("audience_split", "ALL") or "ALL"),
            "programming_instructions": _split(row.get("programming_instructions", "")),
            "option_level_logic": _split(row.get("option_level_logic", "")),
            "termination_logic": _split(row.get("termination_logic", "")),
            "section_objective": str(row.get("section_objective", "") or ""),
            "notes": str(row.get("notes", "") or ""),
            "flag_status": str(row.get("flag_status", "clean") or "clean"),
            "flag_description": str(row.get("flag_description", "") or ""),
        }
        try:
            questions.append(SurveyQuestion.model_validate(payload))
        except ValidationError as exc:
            errors.append({"row": idx, "error": str(exc)})
    return questions, errors


def issue_dashboard_rows(validation: dict | None, consolidated: dict | None = None) -> list[dict]:
    rows: list[dict] = []
    validation = validation or {}
    for row in validation.get("question_issue_rows", []) or []:
        rows.append({
            "Severity": row.get("severity", ""),
            "Issue_Type": row.get("issue_type", ""),
            "Question_ID": row.get("question_id", row.get("q_id", "")),
            "Section": row.get("section", ""),
            "Description": row.get("description", row.get("message", "")),
            "Recommended_Fix": row.get("recommended_fix", row.get("recommended_fix", "")),
            "Status": row.get("status", "Open"),
        })
    for msg in validation.get("blockers", []) or []:
        rows.append({"Severity": "critical", "Issue_Type": "blocker", "Question_ID": "", "Section": "", "Description": msg, "Recommended_Fix": "Resolve before final export.", "Status": "Open"})
    for msg in validation.get("warnings", []) or []:
        rows.append({"Severity": "minor", "Issue_Type": "warning", "Question_ID": "", "Section": "", "Description": msg, "Recommended_Fix": "Review before final export.", "Status": "Open"})
    consolidated = consolidated or {}
    for bucket, status in (("revision_list", "Open"), ("escalated_issues", "Needs human review"), ("auto_fix_issues", "Routed")):
        for issue in consolidated.get(bucket, []) or []:
            rows.append({
                "Severity": issue.get("severity", ""),
                "Issue_Type": issue.get("issue_type", bucket),
                "Question_ID": issue.get("q_id", ""),
                "Section": "",
                "Description": issue.get("description", ""),
                "Recommended_Fix": issue.get("fix_recommendation", ""),
                "Status": issue.get("status", status),
            })
    # De-duplicate exact rows.
    seen = set()
    out = []
    for row in rows:
        key = tuple(row.get(k, "") for k in ("Severity", "Issue_Type", "Question_ID", "Description"))
        if key in seen:
            continue
        seen.add(key)
        out.append(row)
    return out


def _as_dict(value: Any) -> dict:
    if isinstance(value, dict):
        return value
    if hasattr(value, "model_dump"):
        return value.model_dump()
    return {}


def revision_diff_rows(diffs: list[dict] | None) -> list[dict]:
    rows: list[dict] = []
    for raw in diffs or []:
        d = _as_dict(raw)
        rows.append({
            "Change_Type": d.get("change_type", ""),
            "Q_ID_Before": d.get("q_id_before", ""),
            "Q_ID_After": d.get("q_id_after", ""),
            "Fields_Changed": "; ".join(d.get("fields_changed", []) or []),
            "Summary": d.get("summary", ""),
        })
    return rows
