"""Phase 1 hypothesis validation and duplicate detection."""
from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import Iterable, Any

from pydantic import ValidationError

from survey_generator.phase2.models import Hypothesis
from .models import FRAMEWORKS

APPROVAL_STATUSES = ["Approved", "Needs Edit", "Rejected"]
PRIORITIES = ["High", "Medium", "Low"]


def normalize_hypothesis_text(text: str) -> str:
    text = (text or "").lower()
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _as_dict(row: Any) -> dict:
    if isinstance(row, Hypothesis):
        return row.model_dump()
    if isinstance(row, dict):
        return dict(row)
    return {}


def _split_sources(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    if isinstance(value, str):
        return [s.strip() for s in re.split(r"[;,]", value) if s.strip()]
    return []


def prepare_hypothesis_rows(
    rows: Iterable[Any],
    *,
    selected_frameworks: list[str] | None = None,
    study_type: str = "",
    default_audience: str = "",
    source_filenames: list[str] | None = None,
) -> tuple[list[dict], list[dict]]:
    """Clean rows, assign IDs/defaults, and return (rows, validation_issues)."""
    selected_framework_names = set(selected_frameworks or [])
    framework_names = set(FRAMEWORKS.keys())
    source_filenames = source_filenames or []
    cleaned: list[dict] = []
    issues: list[dict] = []
    seen_ids: set[str] = set()
    normalized_seen: dict[str, str] = {}

    for idx, raw in enumerate(rows or [], start=1):
        row = _as_dict(raw)
        text = str(row.get("text", "")).strip()
        if not text:
            issues.append({"row": idx, "severity": "blocker", "field": "text", "message": "Hypothesis text is empty."})
            continue

        hid = str(row.get("hypothesis_id", "")).strip() or f"H{idx:03d}"
        if hid in seen_ids:
            new_hid = f"H{idx:03d}"
            issues.append({"row": idx, "severity": "warning", "field": "hypothesis_id", "message": f"Duplicate ID '{hid}' renamed to '{new_hid}'."})
            hid = new_hid
        seen_ids.add(hid)

        category = str(row.get("category", "")).strip()
        if not category:
            issues.append({"row": idx, "hypothesis_id": hid, "severity": "warning", "field": "category", "message": "Category/construct is blank."})
        elif category in framework_names or category in selected_framework_names:
            issues.append({"row": idx, "hypothesis_id": hid, "severity": "warning", "field": "category", "message": "Category should be a specific construct/dimension, not the framework name."})

        if len(text) < 20:
            issues.append({"row": idx, "hypothesis_id": hid, "severity": "warning", "field": "text", "message": "Hypothesis is very short; check that it is specific and surveyable."})
        text_l = f" {text.lower()} "
        if not any(v in text_l for v in [" is ", " are ", " will ", " drives ", " predict", " associated", " more likely", " less likely", " higher", " lower"]):
            issues.append({"row": idx, "hypothesis_id": hid, "severity": "warning", "field": "text", "message": "Hypothesis may not read like a falsifiable claim."})
        if " who " not in text_l or not (" more likely" in text_l or " less likely" in text_l):
            issues.append({"row": idx, "hypothesis_id": hid, "severity": "warning", "field": "text", "message": "Preferred format is: [audience] who [condition] are more/less likely to [outcome]."})

        norm = normalize_hypothesis_text(text)
        duplicate_of = ""
        if norm in normalized_seen:
            duplicate_of = normalized_seen[norm]
            issues.append({"row": idx, "hypothesis_id": hid, "severity": "blocker", "field": "text", "message": f"Exact duplicate of {duplicate_of}."})
        else:
            for prev_norm, prev_id in normalized_seen.items():
                if SequenceMatcher(None, norm, prev_norm).ratio() >= 0.90:
                    duplicate_of = prev_id
                    issues.append({"row": idx, "hypothesis_id": hid, "severity": "warning", "field": "text", "message": f"Near-duplicate of {prev_id}; consider merging or sharpening."})
                    break
            normalized_seen[norm] = hid

        status = str(row.get("status", row.get("review_status", "Approved"))).strip() or "Approved"
        if status not in APPROVAL_STATUSES:
            issues.append({"row": idx, "hypothesis_id": hid, "severity": "warning", "field": "status", "message": f"Invalid status '{status}' reset to Approved."})
            status = "Approved"

        priority = str(row.get("priority", "Medium")).strip() or "Medium"
        if priority not in PRIORITIES:
            issues.append({"row": idx, "hypothesis_id": hid, "severity": "warning", "field": "priority", "message": f"Invalid priority '{priority}' reset to Medium."})
            priority = "Medium"

        srcs = _split_sources(row.get("source_filenames") or row.get("source_files") or source_filenames)
        if not srcs and source_filenames:
            srcs = source_filenames

        row_messages = [
            str(issue.get("message", ""))
            for issue in issues
            if issue.get("row") == idx and issue.get("hypothesis_id") == hid and issue.get("message")
        ]
        if row_messages and status == "Approved":
            status = "Needs Edit"

        out = {
            "hypothesis_id": hid,
            "text": text,
            "category": category,
            "rationale": str(row.get("rationale", "")).strip(),
            "target_segment": str(row.get("target_segment", "")).strip(),
            "study_type": str(row.get("study_type", study_type)).strip() or study_type,
            "business_question": str(row.get("business_question", "")).strip(),
            "audience": str(row.get("audience", default_audience)).strip() or default_audience,
            "priority": priority,
            "status": status,
            "review_status": status,
            "notes": str(row.get("notes", row.get("review_notes", ""))).strip(),
            "review_notes": str(row.get("notes", row.get("review_notes", ""))).strip(),
            "source_filenames": srcs,
            "source_files": srcs,
            "source_refs": row.get("source_refs", []) if isinstance(row.get("source_refs", []), list) else [],
            "source_excerpt": str(row.get("source_excerpt", "")).strip(),
            "validation_warnings": row_messages,
            "duplicate_of": duplicate_of,
        }
        try:
            Hypothesis.model_validate(out)
            cleaned.append(out)
        except ValidationError as e:
            issues.append({"row": idx, "hypothesis_id": hid, "severity": "blocker", "field": "schema", "message": str(e)})

    for i, row in enumerate(cleaned, start=1):
        row["hypothesis_id"] = f"H{i:03d}"
    return cleaned, issues


def approved_hypotheses(rows: Iterable[dict]) -> list[dict]:
    return [dict(r) for r in rows if str(r.get("status", r.get("review_status", "Approved"))).strip() == "Approved" and str(r.get("text", "")).strip()]


def has_blockers(issues: Iterable[dict]) -> bool:
    return any(i.get("severity") == "blocker" for i in issues or [])

# Compatibility for earlier Phase 1 code/tests.
def validate_hypotheses(hypotheses: Iterable[Hypothesis], brief=None):
    rows, issues = prepare_hypothesis_rows(
        hypotheses,
        selected_frameworks=getattr(brief, "frameworks", None),
        study_type=getattr(brief, "study_type", ""),
        default_audience=getattr(brief, "target_audience", ""),
    )
    return [Hypothesis.model_validate(r) for r in rows], [i.get("message", str(i)) for i in issues]


def approved_hypotheses_only(hypotheses: Iterable[Hypothesis]) -> list[Hypothesis]:
    rows = [h.model_dump() for h in hypotheses]
    return [Hypothesis.model_validate(r) for r in approved_hypotheses(rows)]
