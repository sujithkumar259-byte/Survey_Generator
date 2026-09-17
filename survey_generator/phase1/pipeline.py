"""
Phase 1 — orchestrator, source-aware hypothesis generation, and UI helpers.
"""
from __future__ import annotations

import json
import re
from typing import Any, Callable, Iterable, List

from pydantic import ValidationError

from .models import StudyBrief, SourceDoc, ClarifyingQuestion
from .extract import docs_to_context
from .validation import (
    APPROVAL_STATUSES,
    PRIORITIES,
    approved_hypotheses,
    prepare_hypothesis_rows,
)
from .export import hypotheses_export_xlsx_bytes as _export_hypotheses_xlsx
from .csv_contract import (
    canonical_header_line,
    csv_retry_instruction,
    parse_hypothesis_csv_response,
)
from .frameworks import (
    CUSTOM_FRAMEWORK_NAME,
    category_suggestions,
    selected_framework_prompt_block,
)
from survey_generator.phase2.models import Hypothesis
from survey_generator.llm.client import LLMClient, LLMError
from survey_generator.llm import prompts

ProgressFn = Callable[[str, str, str], None]

# Names used by app.py and older tests.
REVIEW_STATUS_OPTIONS = APPROVAL_STATUSES
PRIORITY_OPTIONS = PRIORITIES


def _noop(*_a, **_k):
    pass


def _brief_block(brief: StudyBrief) -> str:
    return json.dumps(brief.model_dump(), indent=2)


def _as_list(data: Any, key: str) -> list:
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        maybe = data.get(key, [])
        return maybe if isinstance(maybe, list) else []
    return []


def _source_file_names(docs: Iterable[SourceDoc]) -> list[str]:
    return [d.filename for d in docs if (d.text or "").strip()]


def _join(value: Any) -> str:
    if isinstance(value, list):
        return "; ".join(str(v).strip() for v in value if str(v).strip())
    return str(value or "")


def _split(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    return [v.strip() for v in re.split(r"[;\n,]+", str(value or "")) if v.strip()]


def _as_records(edited: Any) -> list[dict]:
    """Accept list-of-dicts or a pandas DataFrame from st.data_editor."""
    if hasattr(edited, "to_dict"):
        try:
            return edited.to_dict("records")
        except Exception:  # noqa: BLE001
            pass
    if isinstance(edited, list):
        return [dict(r) for r in edited if isinstance(r, dict)]
    return []


_TEXT_KEYS = ("text", "hypothesis", "hypothesis_text", "statement", "claim", "research_hypothesis", "hypothesis_statement")
_CATEGORY_KEYS = ("category", "framework", "theme", "domain", "hypothesis_category")
_ID_KEYS = ("hypothesis_id", "id", "hyp_id", "h_id")
_CONTAINER_KEYS = ("hypotheses", "research_hypotheses", "items", "results", "data", "output", "answer")


def _first_present(row: dict, keys: tuple[str, ...], default: Any = "") -> Any:
    for key in keys:
        if key in row and row.get(key) not in (None, ""):
            return row.get(key)
    return default


def _looks_like_hypothesis_dict(row: dict) -> bool:
    return any(str(row.get(k, "")).strip() for k in _TEXT_KEYS)


def _candidate_hypothesis_records(data: Any, *, category_hint: str = "") -> list[dict]:
    """Coerce common LLM response shapes into hypothesis-row dictionaries.

    In practice, models sometimes return {"hypotheses": [...]},
    {"research_hypotheses": [...]}, framework-keyed dictionaries, or even
    a list of strings. This helper keeps Phase 1 usable instead of throwing away
    otherwise useful content because the top-level JSON shape varied.
    """
    records: list[dict] = []

    if data is None:
        return records

    if isinstance(data, str):
        txt = data.strip()
        if txt:
            records.append({"text": txt, "category": category_hint})
        return records

    if isinstance(data, list):
        for item in data:
            records.extend(_candidate_hypothesis_records(item, category_hint=category_hint))
        return records

    if isinstance(data, dict):
        if _looks_like_hypothesis_dict(data):
            row = dict(data)
            row["text"] = str(_first_present(row, _TEXT_KEYS, "")).strip()
            if not row.get("category"):
                row["category"] = str(_first_present(row, _CATEGORY_KEYS, category_hint) or "").strip()
            if not row.get("hypothesis_id"):
                row["hypothesis_id"] = str(_first_present(row, _ID_KEYS, "") or "").strip()
            records.append(row)
            return records

        for key in _CONTAINER_KEYS:
            if key in data:
                records.extend(_candidate_hypothesis_records(data.get(key), category_hint=category_hint))

        # Also support framework-keyed dictionaries, e.g.
        # {"Drivers & barriers": [{...}], "Unmet needs": [{...}]}.
        for key, value in data.items():
            if key in _CONTAINER_KEYS:
                continue
            if isinstance(value, (list, dict, str)):
                hint = category_hint or (key if key not in {"metadata", "notes", "schema"} else "")
                nested = _candidate_hypothesis_records(value, category_hint=hint)
                records.extend(nested)
        return records

    return records


def _default_category(brief: StudyBrief, index: int = 0) -> str:
    frameworks = [f for f in (brief.frameworks or []) if str(f).strip()]
    if not frameworks:
        return ""
    return frameworks[min(index, len(frameworks) - 1)]


def starter_hypotheses_from_context(
    brief: StudyBrief,
    docs: list[SourceDoc],
    *,
    rows_per_framework: int = 2,
) -> list[dict]:
    """Create editable starter hypotheses when the LLM response is unusable.

    These are intentionally marked Needs Edit and carry a warning so users can
    keep working without mistaking the fallback rows for finished AI output.
    """
    frameworks = [f for f in (brief.frameworks or []) if str(f).strip()] or ["HCP · PACE-B Framework"]
    src_files = _source_file_names(docs) or [d.filename for d in docs if d.filename]
    audience = (brief.target_audience or "the target audience").strip()
    focus = (brief.key_decisions or brief.goal or brief.study_type or "the study objective").strip()
    disease = (brief.disease_area or "the market").strip()
    category_pool = category_suggestions(frameworks, getattr(brief, "custom_framework_prompt", "")) or ["Potential", "Attitudes", "Barriers"]
    templates = [
        "{audience} who have stronger opportunity or need related to {focus} are more likely to be receptive to the target product, service, or strategy.",
        "{audience} who perceive barriers, uncertainty, or operational friction in {disease} are less likely to act on the target product, service, or strategy.",
    ]
    rows: list[dict] = []
    for framework in frameworks:
        for template in templates[: max(1, rows_per_framework)]:
            idx = len(rows) + 1
            rows.append({
                "hypothesis_id": f"H{idx:03d}",
                "text": template.format(audience=audience, focus=focus, disease=disease),
                "category": category_pool[(idx - 1) % len(category_pool)],
                "rationale": "Fallback starter row created because the model response could not be converted into usable hypotheses; review and sharpen before approval.",
                "business_question": brief.key_decisions or "",
                "audience": brief.target_audience or "",
                "priority": "Medium",
                "status": "Needs Edit",
                "review_status": "Needs Edit",
                "review_notes": "Review/edit before approving; this row was created as a fallback after failed hypothesis generation.",
                "source_filenames": src_files,
                "source_files": src_files,
                "source_excerpt": "",
                "study_type": brief.study_type or "",
                "target_segment": "",
                "validation_warnings": ["Fallback starter row; review/edit before approval."],
                "duplicate_of": "",
            })
    cleaned, _issues = _prepare_and_attach_validation(rows, brief=brief, source_filenames=src_files)
    fallback_warning = "Fallback starter row; review/edit before approval."
    for row in cleaned:
        warnings = list(row.get("validation_warnings") or [])
        if fallback_warning not in warnings:
            warnings.append(fallback_warning)
        row["validation_warnings"] = warnings
        row["status"] = "Needs Edit"
        row["review_status"] = "Needs Edit"
        row["approval_status"] = "Needs Edit"
    return cleaned


def _row_dict(row: Any) -> dict:
    if hasattr(row, "model_dump"):
        return row.model_dump()
    if isinstance(row, dict):
        return dict(row)
    return {}

def _prepare_and_attach_validation(
    rows: Iterable[Any],
    *,
    brief: StudyBrief | None = None,
    source_filenames: list[str] | None = None,
) -> tuple[list[dict], list[dict]]:
    """Run deterministic hypothesis QA and attach warnings/status to rows."""
    cleaned, issues = prepare_hypothesis_rows(
        rows,
        selected_frameworks=getattr(brief, "frameworks", None),
        study_type=getattr(brief, "study_type", ""),
        default_audience=getattr(brief, "target_audience", ""),
        source_filenames=source_filenames,
    )
    issues_by_id: dict[str, list[str]] = {}
    severe_by_id: set[str] = set()
    for issue in issues:
        hid = str(issue.get("hypothesis_id", "") or "").strip()
        if not hid and isinstance(issue.get("row"), int) and 0 < int(issue["row"]) <= len(cleaned):
            hid = cleaned[int(issue["row"]) - 1].get("hypothesis_id", "")
        if hid:
            issues_by_id.setdefault(hid, []).append(str(issue.get("message", issue)))
            if issue.get("severity") in {"warning", "blocker"}:
                severe_by_id.add(hid)

    for row in cleaned:
        hid = row.get("hypothesis_id", "")
        existing = _split(row.get("validation_warnings", []))
        merged: list[str] = []
        for msg in existing + issues_by_id.get(hid, []):
            if msg and msg not in merged:
                merged.append(msg)
        row["validation_warnings"] = merged
        row["review_status"] = row.get("review_status", row.get("status", "Approved"))
        row["status"] = row["review_status"]
        row["approval_status"] = row["review_status"]
        if merged and hid in severe_by_id and row["review_status"] == "Approved":
            row["review_status"] = "Needs Edit"
            row["status"] = "Needs Edit"
            row["approval_status"] = "Needs Edit"
    return cleaned, issues



# ---- Step A: clarifier ------------------------------------------------------
def run_clarifier(
    client: LLMClient,
    brief: StudyBrief,
    docs: List[SourceDoc],
    progress: ProgressFn = _noop,
) -> List[ClarifyingQuestion]:
    progress("Clarifier", "start", "Reading files + brief, drafting clarifying questions")
    s, u = prompts.p1_clarifier(
        brief_block=_brief_block(brief),
        frameworks=selected_framework_prompt_block(brief.frameworks, getattr(brief, "custom_framework_prompt", "")),
        files_block=docs_to_context(docs),
    )
    data = client.complete_json(s, u, expected_type=(dict, list))
    out: List[ClarifyingQuestion] = []
    for d in _as_list(data, "questions"):
        try:
            out.append(ClarifyingQuestion.model_validate(d))
        except ValidationError:
            continue
    progress("Clarifier", "done", f"{len(out)} clarifying questions")
    return out


# ---- Step B: generator ------------------------------------------------------
def _complete_generator_text(client: LLMClient, system: str, user: str) -> str:
    """Return raw model text for the Phase 1 CSV generator.

    Real LLMClient instances expose complete_text. Some tests/mocks from older
    versions expose only complete_json, so this helper keeps those mocks usable
    while the production contract moves to simple CSV text.
    """
    if hasattr(client, "complete_text"):
        return str(client.complete_text(system, user))
    if hasattr(client, "complete_json"):
        data = client.complete_json(system, user, expected_type=(list, dict, str))
        return data if isinstance(data, str) else json.dumps(data)
    raise LLMError("LLM client does not support text completion.")


def run_generator(
    client: LLMClient,
    brief: StudyBrief,
    docs: List[SourceDoc],
    clarifications: List[dict[str, str]] | None = None,
    progress: ProgressFn = _noop,
) -> List[Hypothesis]:
    progress("Generator", "start", "Generating hypotheses from brief, answers & files")
    clarifications = clarifications or []
    clar_text = json.dumps(clarifications, indent=2) if clarifications else "(none provided)"
    s, u = prompts.p1_generator(
        brief_block=_brief_block(brief),
        study_type=brief.study_type or "(unspecified)",
        frameworks=selected_framework_prompt_block(brief.frameworks, getattr(brief, "custom_framework_prompt", "")),
        clarifications=clar_text,
        files_block=docs_to_context(docs),
    )

    src_files = _source_file_names(docs)
    max_attempts = max(1, int(getattr(client, "max_json_retries", 2) or 0) + 1)
    prompt = u
    last_parse = None
    parsed_rows: list[dict[str, str]] = []
    detected_format = "unknown"

    for attempt in range(max_attempts):
        raw_text = _complete_generator_text(client, s, prompt)
        parse = parse_hypothesis_csv_response(
            raw_text,
            allowed_categories=category_suggestions(brief.frameworks, getattr(brief, "custom_framework_prompt", "")),
        )
        last_parse = parse
        if parse.rows:
            parsed_rows = parse.rows
            detected_format = parse.detected_format
            if parse.detected_format != "csv":
                progress("Generator", "recover", f"Recovered hypotheses from {parse.detected_format} output")
            break
        if attempt < max_attempts - 1:
            progress("Generator", "retry", "Model response did not match the required four-column CSV; retrying with stricter instructions")
            prompt = u + csv_retry_instruction(
                parse,
                category_suggestions(brief.frameworks, getattr(brief, "custom_framework_prompt", "")),
            )

    if not parsed_rows:
        issue_text = "; ".join((last_parse.issues if last_parse else []) or [])
        raise LLMError(
            "The model did not return usable hypotheses in the required CSV format. "
            f"Expected header: {canonical_header_line()}. "
            f"Parser detail: {issue_text or 'No parseable rows were found.'}"
        )

    raw_rows: list[dict] = []
    for i, d in enumerate(parsed_rows, start=1):
        row = dict(d)
        category = str(row.get("category", "") or "").strip()
        if not category:
            category = (category_suggestions(brief.frameworks, getattr(brief, "custom_framework_prompt", "")) or [_default_category(brief)])[0]
        warnings = []
        if last_parse and last_parse.issues:
            warnings = [w for w in last_parse.issues if w and "No usable" not in w]
        row.update({
            "hypothesis_id": row.get("hypothesis_id") or f"H{i:03d}",
            "category": category or (category_suggestions(brief.frameworks, getattr(brief, "custom_framework_prompt", "")) or [_default_category(brief)])[0],
            "study_type": brief.study_type,
            "target_segment": "",
            "audience": brief.target_audience,
            "business_question": brief.key_decisions,
            "status": "Approved",
            "review_status": "Approved",
            "priority": "Medium",
            "source_filenames": src_files,
            "source_files": src_files,
            "source_excerpt": "",
            "validation_warnings": warnings,
        })
        raw_rows.append(row)

    cleaned, _issues = _prepare_and_attach_validation(
        raw_rows,
        brief=brief,
        source_filenames=src_files,
    )
    out: List[Hypothesis] = []
    dropped = 0
    for row in cleaned:
        try:
            out.append(Hypothesis.model_validate(row))
        except ValidationError:
            dropped += 1
            continue
    if not out:
        raise LLMError(
            "The model returned hypothesis rows, but none passed schema validation. "
            "Try regenerating, or use the editable fallback rows to continue."
        )
    detail = f"{len(out)} hypotheses generated from {detected_format} output; validation statuses applied"
    if dropped:
        detail += f"; {dropped} malformed row(s) dropped"
    progress("Generator", "done", detail)
    return out


# ---- UI/editing/export helpers ---------------------------------------------
def editor_rows_from_hypotheses(hypotheses: Iterable[Any]) -> list[dict]:
    """Return Streamlit data_editor rows with human-review fields."""
    rows: list[dict] = []
    for i, h in enumerate(hypotheses or [], start=1):
        h = _row_dict(h)
        status = h.get("review_status") or h.get("status") or h.get("approval_status") or "Approved"
        if status not in REVIEW_STATUS_OPTIONS:
            status = "Needs Edit"
        priority = h.get("priority") or "Medium"
        if priority not in PRIORITY_OPTIONS:
            priority = "Medium"
        rows.append({
            "hypothesis_id": h.get("hypothesis_id") or f"H{i:03d}",
            "text": h.get("text", ""),
            "category": h.get("category", ""),
            "rationale": h.get("rationale", ""),
            "business_question": h.get("business_question", ""),
            "audience": h.get("audience", ""),
            "priority": priority,
            "review_status": status,
            "review_notes": h.get("review_notes", h.get("notes", "")),
            "source_files": _join(h.get("source_files") or h.get("source_filenames") or []),
            "source_excerpt": h.get("source_excerpt") or _join(h.get("source_refs", [])),
            "validation_warnings": _join(h.get("validation_warnings", [])),
            "duplicate_of": h.get("duplicate_of", ""),
            "study_type": h.get("study_type", ""),
            "target_segment": h.get("target_segment", ""),
        })
    return rows


# Older compatibility name.
rows_for_editor = editor_rows_from_hypotheses


def normalise_editor_rows(
    edited: Any,
    brief: StudyBrief,
    docs: List[SourceDoc],
) -> tuple[list[dict], list[dict]]:
    """Clean edited hypothesis rows, rerun validation, and attach issue text."""
    source_filenames = _source_file_names(docs) or [d.filename for d in docs]
    raw_rows: list[dict] = []
    for row in _as_records(edited):
        text = str(row.get("text", "") or "").strip()
        if not text:
            continue
        status = row.get("review_status") or row.get("status") or row.get("approval_status") or "Approved"
        raw_rows.append({
            "hypothesis_id": row.get("hypothesis_id", ""),
            "text": text,
            "category": row.get("category", ""),
            "rationale": row.get("rationale", ""),
            "business_question": row.get("business_question", ""),
            "audience": row.get("audience", ""),
            "priority": row.get("priority", "Medium"),
            "review_status": status,
            "status": status,
            "review_notes": row.get("review_notes", row.get("notes", "")),
            "notes": row.get("notes", row.get("review_notes", "")),
            "source_files": _split(row.get("source_files") or row.get("source_filenames") or ""),
            "source_filenames": _split(row.get("source_files") or row.get("source_filenames") or ""),
            "source_excerpt": row.get("source_excerpt", ""),
            "study_type": row.get("study_type", brief.study_type),
            "target_segment": row.get("target_segment", ""),
        })

    return _prepare_and_attach_validation(
        raw_rows,
        brief=brief,
        source_filenames=source_filenames,
    )


# Compatibility alias used by older tests/code.
def validate_hypothesis_rows(
    rows: Iterable[Any],
    brief: StudyBrief | None = None,
    source_filenames: list[str] | None = None,
) -> tuple[list[dict], list[dict]]:
    return _prepare_and_attach_validation(rows, brief=brief, source_filenames=source_filenames)


def approved_hypotheses_only(rows: Iterable[Any]) -> list[dict]:
    """Return only rows explicitly approved for Phase 2."""
    return approved_hypotheses([_row_dict(r) for r in rows or []])


# Older compatibility name.
approved_rows_for_phase2 = approved_hypotheses_only


def hypotheses_export_xlsx_bytes(rows: Iterable[Any], docs: List[SourceDoc], validation_issues=None) -> bytes:
    return _export_hypotheses_xlsx([_row_dict(r) for r in rows or []], docs, validation_issues)


# Older compatibility name.
build_hypothesis_export_xlsx = hypotheses_export_xlsx_bytes
