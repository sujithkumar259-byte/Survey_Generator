"""Phase 2 post-processing helpers: renumbering, revision merge, diffs."""
from __future__ import annotations

import re
from collections import defaultdict
from typing import Iterable, List

from .models import Issue, SectionProposal, SurveyQuestion

TERMINAL_TARGETS = {"END", "TERMINATE", "TERMINATED", "SCREENOUT", "SUBMIT"}


def _safe_id_part(value: str, fallback: str) -> str:
    text = re.sub(r"[^A-Za-z0-9]+", "_", str(value or "").strip()).strip("_").upper()
    return text or fallback


def renumber_questions(questions: List[SurveyQuestion], sections: List[SectionProposal]) -> tuple[List[SurveyQuestion], dict[str, str]]:
    """Assign deterministic section-order IDs and update routing targets.

    Returns (renumbered_questions, old_id_to_new_id).
    """
    section_order = {s.section_id: i for i, s in enumerate(sections)}
    title_to_id = {s.section_title: s.section_id for s in sections}
    section_title = {s.section_id: s.section_title for s in sections}

    def sort_key(pair):
        idx, q = pair
        sid = q.section_id or title_to_id.get(q.section_title) or title_to_id.get(q.section) or q.section_id
        return (section_order.get(sid, 999), idx)

    ordered = [q.model_copy(deep=True) for _idx, q in sorted(enumerate(questions), key=sort_key)]
    counters: dict[str, int] = defaultdict(int)
    id_map: dict[str, str] = {}
    for q in ordered:
        if not q.section_id:
            q.section_id = title_to_id.get(q.section_title) or title_to_id.get(q.section) or _safe_id_part(q.section or q.section_title, "SEC")
        if q.section_id in section_title:
            q.section_title = section_title[q.section_id]
            q.section = section_title[q.section_id]
        elif not q.section_title:
            q.section_title = q.section or q.section_id
        counters[q.section_id] += 1
        prefix = _safe_id_part(q.section_id, "SEC")
        old = q.q_id
        new = f"{prefix}_Q{counters[q.section_id]:02d}"
        q.q_id = new
        q.display_order = new
        if old:
            id_map[old] = new
    for q in ordered:
        if q.routing and q.routing.next:
            target = q.routing.next.strip()
            if target in id_map:
                q.routing.next = id_map[target]
    return ordered, id_map


def parse_revision_payload(data) -> tuple[list[SurveyQuestion], list[str]]:
    """Accept either a legacy JSON array or the newer reviser object schema."""
    from pydantic import ValidationError

    raw_questions = data
    deleted_ids: list[str] = []
    if isinstance(data, dict):
        deleted_raw = data.get("deleted_question_ids") or data.get("delete_question_ids") or data.get("deleted_q_ids") or []
        if isinstance(deleted_raw, str):
            deleted_ids = [s.strip() for s in re.split(r"[;,\n]+", deleted_raw) if s.strip()]
        elif isinstance(deleted_raw, list):
            deleted_ids = [str(v).strip() for v in deleted_raw if str(v).strip()]
        raw_questions = (
            data.get("revised_questions")
            or data.get("inserted_questions")
            or data.get("questions")
            or []
        )
        if data.get("revised_questions") and data.get("inserted_questions"):
            raw_questions = list(data.get("revised_questions") or []) + list(data.get("inserted_questions") or [])
    out: list[SurveyQuestion] = []
    for row in raw_questions or []:
        if isinstance(row, dict) and "question" in row and isinstance(row["question"], dict):
            row = row["question"]
        try:
            out.append(SurveyQuestion.model_validate(row))
        except ValidationError:
            continue
    return out, deleted_ids


def _issue_anchor_for_new_question(new_q: SurveyQuestion, issues: Iterable[Issue], current: list[SurveyQuestion]) -> int:
    """Find the best insertion index for an inserted question."""
    by_id = {q.q_id: idx for idx, q in enumerate(current)}
    # Prefer a missing_question issue in the same section or covering the same hypothesis.
    for issue in issues or []:
        if issue.issue_type == "missing_question" or "missing" in issue.description.lower():
            if issue.q_id and issue.q_id in by_id:
                return by_id[issue.q_id] + 1
    # Otherwise insert after the last question in the same section.
    for idx in range(len(current) - 1, -1, -1):
        q = current[idx]
        if (new_q.section_id and q.section_id == new_q.section_id) or (new_q.section and q.section == new_q.section):
            return idx + 1
    return len(current)


def apply_revisions(
    questions: List[SurveyQuestion],
    revised: List[SurveyQuestion],
    deleted_question_ids: Iterable[str] | None = None,
    issues: Iterable[Issue] | None = None,
) -> tuple[List[SurveyQuestion], list[dict]]:
    """Replace, insert, and delete questions returned by the reviser.

    Returns (updated_questions, diff_rows).
    """
    deleted = {str(qid).strip() for qid in (deleted_question_ids or []) if str(qid).strip()}
    existing_by_id = {q.q_id: q for q in questions}
    updated = [q.model_copy(deep=True) for q in questions if q.q_id not in deleted]
    revised_by_id = {q.q_id: q for q in revised if q.q_id in existing_by_id}
    diffs: list[dict] = []

    for idx, q in enumerate(updated):
        replacement = revised_by_id.get(q.q_id)
        if replacement:
            before = q.model_dump()
            after = replacement.model_dump()
            changed_fields = [k for k in sorted(after.keys()) if before.get(k) != after.get(k)]
            if changed_fields:
                diffs.append({
                    "Action": "Modified",
                    "Question_ID": q.q_id,
                    "Changed_Fields": ", ".join(changed_fields),
                    "Before_Text": q.question_text,
                    "After_Text": replacement.question_text,
                })
            updated[idx] = replacement

    for qid in deleted:
        if qid in existing_by_id:
            diffs.append({
                "Action": "Deleted",
                "Question_ID": qid,
                "Changed_Fields": "deleted_question_ids",
                "Before_Text": existing_by_id[qid].question_text,
                "After_Text": "",
            })

    current_ids = {q.q_id for q in updated}
    for new_q in [q for q in revised if q.q_id not in existing_by_id and q.q_id not in current_ids]:
        insert_at = _issue_anchor_for_new_question(new_q, issues or [], updated)
        updated.insert(insert_at, new_q)
        current_ids.add(new_q.q_id)
        diffs.append({
            "Action": "Inserted",
            "Question_ID": new_q.q_id,
            "Changed_Fields": "new_question",
            "Before_Text": "",
            "After_Text": new_q.question_text,
        })

    return updated, diffs
