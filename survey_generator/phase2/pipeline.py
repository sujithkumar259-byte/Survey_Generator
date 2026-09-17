"""
Phase 2 — agent pipeline.

Step 1 proposes sections; the UI then lets a human edit/lock those sections.
After the gate, Steps 3-11 map hypotheses to stable section_id values, draft
questions per section, validate coverage/routing, and run the review loop.
"""
from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Callable, Dict, List, TypeVar

from pydantic import BaseModel, ValidationError

from .models import (
    ConsolidatedResult,
    HMapping,
    Hypothesis,
    Issue,
    SectionProposal,
    StudySetup,
    SurveyQuestion,
)
from .review import question_diffs
from .validator import validate
from survey_generator.llm import prompts
from survey_generator.llm.client import LLMClient, LLMError

MAX_RETRIES = 3
MAX_REVIEW_ROUNDS = 1   # LLM quality rounds (reviewers → consolidate → edit → revise)
MAX_REPAIR_ROUNDS = 3   # deterministic convergence rounds: re-validate → fix → re-validate
REVIEW_CHUNK_SIZE = 25
REVIEW_MAX_WORKERS = 6  # parallel reviewer calls across section chunks
QUESTION_BUDGET = 90    # soft backstop — only trims a grossly over-large survey; coverage preserved

# Map the deterministic validator's severities onto the Issue model's severities.
_VALIDATOR_SEVERITY = {"blocker": "critical", "burden": "minor", "warning": "minor"}
# Routing targets the validator accepts as terminal (kept verbatim, never rewritten).
_TERMINAL_ROUTES = {"END", "TERMINATE", "TERMINATION", "SUBMIT", "SCREENOUT", "SCREEN_OUT", "COMPLETE"}
# RATING_* types the validator requires to carry at least one statement row in options.
_RATING_TYPES = {"RATING_7", "RATING_7_IMPORTANCE", "RATING_7_SATISFACTION", "RATING_7_LIKELIHOOD", "RATING_7_FAMILIARITY"}
ProgressFn = Callable[[str, str, str], None]
ModelT = TypeVar("ModelT", bound=BaseModel)


def _noop(*_a, **_k):
    pass


_GENERIC_LIST_WRAPPER_KEYS = (
    "items", "results", "data", "output", "answer", "rows", "records",
)

_MODEL_LIST_WRAPPER_KEYS = {
    "SectionProposal": (
        "sections", "section_proposals", "suggested_sections", "survey_sections",
        "questionnaire_sections", *_GENERIC_LIST_WRAPPER_KEYS,
    ),
    "HMapping": (
        "mappings", "hypothesis_mappings", "h_mappings", "mapping",
        "hypothesis_to_section_mapping", *_GENERIC_LIST_WRAPPER_KEYS,
    ),
    "SurveyQuestion": (
        "questions", "survey_questions", "drafted_questions", "revised_questions",
        "inserted_questions", "questionnaire", *_GENERIC_LIST_WRAPPER_KEYS,
    ),
    "Issue": ("issues", "review_issues", *_GENERIC_LIST_WRAPPER_KEYS),
}


def _coerce_json_list(data: Any, model_cls: type[BaseModel] | None = None) -> list[Any]:
    """Recover JSON-array payloads from common model wrapper shapes.

    Live LLMs often return useful output as {"questions": [...]},
    {"sections": [...]}, or {"data": [...]} even when instructed to return a
    bare array. This helper prevents those harmless wrappers from crashing the
    pipeline with "Expected JSON type list; got dict".
    """
    if isinstance(data, list):
        return data
    if not isinstance(data, dict):
        return []

    model_name = model_cls.__name__ if model_cls is not None else ""
    candidate_keys = _MODEL_LIST_WRAPPER_KEYS.get(model_name, _GENERIC_LIST_WRAPPER_KEYS)
    for key in candidate_keys:
        if key not in data:
            continue
        value = data.get(key)
        if isinstance(value, list):
            return value
        if isinstance(value, dict):
            nested = _coerce_json_list(value, model_cls)
            if nested:
                return nested

    # If the dict itself looks like a single model object, accept it as a one-row
    # array. This helps when a one-section/one-question response is returned as
    # an object instead of a list.
    if model_cls is not None:
        field_names = set(getattr(model_cls, "model_fields", {}).keys())
        alias_names = {getattr(field, "alias", None) for field in getattr(model_cls, "model_fields", {}).values()}
        model_keys = {k for k in field_names | alias_names if k}
        if len(model_keys.intersection(data.keys())) >= 2:
            return [data]

    # Last resort: if there is exactly one list-valued field, use it; if there
    # are several, use the first non-empty list.
    list_values = [v for v in data.values() if isinstance(v, list)]
    non_empty = [v for v in list_values if v]
    if len(list_values) == 1:
        return list_values[0]
    if non_empty:
        return non_empty[0]
    return []


def _model_list(client: LLMClient, system: str, user: str, model_cls: type[ModelT], label: str,
                *, min_items: int = 0) -> list[ModelT]:
    """Call an LLM JSON-array step and retry malformed/invalid shapes.

    Accepts both a bare JSON array and common dict wrappers like
    {"questions": [...]}, {"sections": [...]}, and {"mappings": [...]}.
    """
    last_error = ""
    json_retries = getattr(client, "max_json_retries", getattr(client, "json_retries", 2))
    for _attempt in range(json_retries + 1):
        data = client.complete_json(system, user, expected_type=(list, dict))
        items = _coerce_json_list(data, model_cls)
        out: list[ModelT] = []
        validation_errors = 0
        for item in items:
            # Some insertion wrappers look like {"insert_after_q_id": "...", "question": {...}}.
            if isinstance(item, dict) and "question" in item and isinstance(item["question"], dict):
                item = item["question"]
            try:
                out.append(model_cls.model_validate(item))
            except ValidationError:
                validation_errors += 1
        if len(out) >= min_items and validation_errors == 0:
            return out
        shape = type(data).__name__
        if isinstance(data, dict):
            shape += " keys=" + ",".join(list(data.keys())[:8])
        last_error = (
            f"{label} returned {len(out)} valid rows and {validation_errors} invalid rows "
            f"from response shape {shape}."
        )
        user = user + (
            "\n\nYour previous response did not match the requested schema. "
            f"{last_error} Return ONLY a JSON array with valid objects. Do not wrap it in an object."
        )
    raise LLMError(last_error or f"{label} returned no valid rows.")


def _normalise_mappings(mappings: list[HMapping], sections: list[SectionProposal]) -> list[HMapping]:
    title_by_id = {s.section_id: s.section_title for s in sections}
    id_by_title = {s.section_title: s.section_id for s in sections}
    normalised: list[HMapping] = []
    for m in mappings:
        # Back-compat: if an older prompt put the title in `section`, infer id.
        if not m.section_id:
            if m.section in id_by_title:
                m.section_id = id_by_title[m.section]
            elif m.section_title in id_by_title:
                m.section_id = id_by_title[m.section_title]
            elif m.section in title_by_id:
                m.section_id = m.section
        if m.section_id and not m.section_title:
            m.section_title = title_by_id.get(m.section_id, m.section_title)
        if m.section_id and not m.section:
            m.section = title_by_id.get(m.section_id, m.section)
        if m.section_id in title_by_id:
            # Canonicalize title so exact-title drift does not affect drafting.
            m.section_title = title_by_id[m.section_id]
            m.section = title_by_id[m.section_id]
        normalised.append(m)
    return normalised


def _mapping_errors(mappings: list[HMapping], hyps: list[Hypothesis], sections: list[SectionProposal]) -> list[str]:
    errors: list[str] = []
    valid_section_ids = {s.section_id for s in sections}
    valid_hids = [h.hypothesis_id for h in hyps]
    counts = {hid: 0 for hid in valid_hids}

    for m in mappings:
        if m.hypothesis_id not in counts:
            errors.append(f"Mapping references unknown hypothesis_id '{m.hypothesis_id}'.")
            continue
        counts[m.hypothesis_id] += 1
        if not m.section_id:
            errors.append(f"Hypothesis {m.hypothesis_id} has no section_id.")
        elif m.section_id not in valid_section_ids:
            errors.append(f"Hypothesis {m.hypothesis_id} maps to invalid section_id '{m.section_id}'.")

    for hid, count in counts.items():
        if count == 0:
            errors.append(f"Hypothesis {hid} is missing from the section mapping.")
        elif count > 1:
            errors.append(f"Hypothesis {hid} appears {count} times in the section mapping.")
    return errors


def _require_valid_mappings(mappings: list[HMapping], hyps: list[Hypothesis],
                            sections: list[SectionProposal]) -> list[HMapping]:
    mappings = _normalise_mappings(mappings, sections)
    errors = _mapping_errors(mappings, hyps, sections)
    if errors:
        raise LLMError("Invalid hypothesis-to-section mapping:\n" + "\n".join(f"- {e}" for e in errors[:20]))
    return mappings


# ---- Step 1 -----------------------------------------------------------------
def run_section_suggester(client: LLMClient, study: StudySetup,
                          hyps: List[Hypothesis], progress: ProgressFn = _noop) -> List[SectionProposal]:
    progress("Section Suggester", "start", "Proposing sections from hypotheses")
    system, user = prompts.section_suggester(study, hyps)
    sections = _model_list(client, system, user, SectionProposal, "Section Suggester", min_items=1)
    progress("Section Suggester", "done", f"{len(sections)} sections proposed")
    return sections


def _chunks_for_review(questions: list[SurveyQuestion], max_questions: int = REVIEW_CHUNK_SIZE) -> list[list[SurveyQuestion]]:
    if len(questions) <= max_questions:
        return [questions]
    chunks: list[list[SurveyQuestion]] = []
    by_section: dict[str, list[SurveyQuestion]] = {}
    for q in questions:
        by_section.setdefault(q.section_id or q.section, []).append(q)
    for sec_questions in by_section.values():
        for i in range(0, len(sec_questions), max_questions):
            chunks.append(sec_questions[i:i + max_questions])
    return chunks or [questions]


def _deterministic_missing_question_issues(report, mappings: list[HMapping], sections: list[SectionProposal]) -> list[Issue]:
    if not report.uncovered_hypotheses:
        return []
    mapping_by_h = {m.hypothesis_id: m for m in mappings}
    section_title_by_id = {s.section_id: s.section_title for s in sections}
    issues: list[Issue] = []
    for hid in report.uncovered_hypotheses:
        mapping = mapping_by_h.get(hid)
        section_id = mapping.section_id if mapping else ""
        section_title = section_title_by_id.get(section_id, mapping.section_title if mapping else "")
        issues.append(Issue(
            issue_id=f"DET_MISSING_{hid}",
            q_id="",
            severity="major",
            issue_type="missing_question",
            description=f"Hypothesis {hid} is not covered by any question.",
            fix_recommendation=(
                f"Create one new question covering hypothesis {hid}. "
                f"Place it in section_id '{section_id}' ({section_title}). "
                "Set hypothesis_ids to include this hypothesis_id."
            ),
            source_agent="deterministic_validator",
        ))
    return issues


def _cap_questions(questions: list[SurveyQuestion], max_total: int = QUESTION_BUDGET) -> tuple[list[SurveyQuestion], int]:
    """Trim an over-large drafted survey down to ~max_total questions WITHOUT losing
    hypothesis coverage. Pass 1 keeps every question that newly covers a hypothesis;
    pass 2 fills the remaining budget in original order (so the screener/profile, which
    come first, are kept). Returns (kept_questions, trimmed_count)."""
    if len(questions) <= max_total:
        return questions, 0
    covered: set[str] = set()
    keep_ids: set[int] = set()
    for q in questions:                       # pass 1 — preserve coverage
        hs = q.hypothesis_ids or []
        if any(h not in covered for h in hs):
            keep_ids.add(id(q))
            covered.update(hs)
    for q in questions:                       # pass 2 — fill the budget, in order
        if len(keep_ids) >= max_total:
            break
        keep_ids.add(id(q))
    kept = [q for q in questions if id(q) in keep_ids]
    return kept, len(questions) - len(kept)


def _collect_review_issues(client: LLMClient, questions: list[SurveyQuestion], study: StudySetup,
                           section_titles_in_order: list[str], progress: ProgressFn,
                           domain_key: str) -> tuple[list[Issue], int]:
    """Run all reviewers (Domain Expert / Flow Checker / Quality Reviewer) over the
    per-section chunks CONCURRENTLY. The calls are independent, so a thread pool turns
    chunks×3 serial round-trips into a few parallel waves — the main Phase-2 speedup."""
    chunks = _chunks_for_review(questions)
    chunk_count = len(chunks)
    progress("Review", "start",
             f"Reviewing {chunk_count} section chunk(s) × 2 per-section reviewers "
             f"+ 1 whole-survey flow check, in parallel ({domain_key})")

    def _domain(chunk):
        s, u = prompts.conditional_domain_expert(chunk, study)
        return _parse_issues(client.complete_json(s, u, expected_type=(list, dict)))

    def _quality(chunk):
        s, u = prompts.quality_reviewer(chunk)
        return _parse_issues(client.complete_json(s, u, expected_type=(list, dict)))

    def _flow_all():
        # Flow/order/priming/fatigue are whole-survey properties, so the flow checker
        # must see the entire ordered survey at once — not one section chunk. Running it
        # per chunk gave it only a partial view and produced spurious, duplicated flags.
        s, u = prompts.survey_flow_checker(questions, section_titles_in_order)
        return _parse_issues(client.complete_json(s, u, expected_type=(list, dict)))

    jobs = []
    for chunk in chunks:
        jobs.append(lambda c=chunk: _domain(c))
        jobs.append(lambda c=chunk: _quality(c))
    jobs.append(_flow_all)  # one call over the full survey, alongside the per-section reviewers

    issues: list[Issue] = []
    failed = 0
    with ThreadPoolExecutor(max_workers=min(REVIEW_MAX_WORKERS, max(1, len(jobs)))) as ex:
        for fut in as_completed([ex.submit(j) for j in jobs]):
            try:
                issues += fut.result()
            except Exception:  # noqa: BLE001 — one reviewer failing shouldn't sink the round
                failed += 1

    note = f"{len(issues)} issues across {chunk_count} chunk(s)"
    if failed:
        note += f" ({failed} reviewer call(s) failed and were skipped)"
    progress("Review", "done", note)
    return issues, chunk_count


CONSOLIDATE_BATCH_SIZE = 40  # max issues per consolidator call (sub-splits a huge section)


def _section_of_issue(issue: Issue, section_by_qid: dict[str, str]) -> str:
    """Best-effort section bucket for an issue: by its question's section, else by a
    section_id named in the fix_recommendation (missing-question inserts), else ''."""
    if issue.q_id and issue.q_id in section_by_qid:
        return section_by_qid[issue.q_id]
    m = re.search(r"section_id ['\"]?([A-Za-z0-9_-]+)['\"]?", issue.fix_recommendation or "")
    if m:
        return m.group(1)
    return ""


def _consolidate_issues(client: LLMClient, issues: list[Issue], questions: list[SurveyQuestion],
                        section_ids_in_order: list[str]) -> ConsolidatedResult:
    """Triage review issues into revision/auto-fix/escalated lists. Split the issues by
    section and run the groups CONCURRENTLY, then merge — the same parallelism the reviewers
    already use. A big survey no longer triages in one slow, truncation-prone call; each
    group's output is small enough to never hit the response-size ceiling.

    Cross-reviewer dedup on the same question is preserved (all of a question's issues land
    in the same group). Only cross-section dedup is given up, which is immaterial because
    every issue is question-scoped."""
    if not issues:
        return ConsolidatedResult()
    section_by_qid = {q.q_id: (q.section_id or q.section) for q in questions}
    buckets: dict[str, list[Issue]] = {}
    for issue in issues:
        buckets.setdefault(_section_of_issue(issue, section_by_qid) or "_unassigned", []).append(issue)
    ordered = [s for s in section_ids_in_order if s in buckets] + \
              [k for k in buckets if k not in section_ids_in_order]
    groups: list[list[Issue]] = []
    for key in ordered:
        grp = sorted(buckets[key], key=lambda i: i.q_id)  # keep same-question issues adjacent
        for i in range(0, len(grp), CONSOLIDATE_BATCH_SIZE):
            groups.append(grp[i:i + CONSOLIDATE_BATCH_SIZE])

    def _triage(group: list[Issue]) -> ConsolidatedResult:
        s, u = prompts.final_consolidator(json.dumps([i.model_dump() for i in group], indent=2))
        return _parse_consolidated(client.complete_json(s, u, expected_type=(dict, list)))

    merged = ConsolidatedResult()
    with ThreadPoolExecutor(max_workers=min(REVIEW_MAX_WORKERS, max(1, len(groups)))) as ex:
        for fut in as_completed([ex.submit(_triage, g) for g in groups]):
            try:
                part = fut.result()
            except Exception:  # noqa: BLE001 — a failed group shouldn't sink the whole triage
                continue
            merged.revision_list.extend(part.revision_list)
            merged.escalated_issues.extend(part.escalated_issues)
            merged.auto_fix_issues.extend(part.auto_fix_issues)
            merged.major_issue_count += part.major_issue_count
    return merged


def _revise_questions(client: LLMClient, questions: list[SurveyQuestion], revision_list: list[Issue],
                      section_ids_in_order: list[str]) -> tuple[list[SurveyQuestion], list[str]]:
    """Rewrite/insert flagged questions. Split the work by section and run the groups
    CONCURRENTLY, then concatenate — so the reviser never emits the whole survey in one slow,
    truncation-prone response. Each question lives in exactly one section, so no question is
    revised twice."""
    if not revision_list:
        return [], []
    section_by_qid = {q.q_id: (q.section_id or q.section) for q in questions}
    questions_by_section: dict[str, list[SurveyQuestion]] = {}
    for q in questions:
        questions_by_section.setdefault(q.section_id or q.section, []).append(q)
    default_section = section_ids_in_order[0] if section_ids_in_order else ""

    issues_by_section: dict[str, list[Issue]] = {}
    for issue in revision_list:
        issues_by_section.setdefault(_section_of_issue(issue, section_by_qid) or default_section, []).append(issue)

    def _revise(sec: str, sec_issues: list[Issue]) -> tuple[list[SurveyQuestion], list[str]]:
        flagged_ids = {i.q_id for i in sec_issues if i.q_id}
        flagged_qs = [q for q in questions if q.q_id in flagged_ids]
        # Missing-question inserts need the section's existing questions for placement context.
        if any(i.issue_type == "missing_question" for i in sec_issues):
            for q in questions_by_section.get(sec, []):
                if q not in flagged_qs:
                    flagged_qs.append(q)
        if not flagged_qs:
            flagged_qs = questions_by_section.get(sec, [])[:REVIEW_CHUNK_SIZE]
        s, u = prompts.question_reviser(flagged_qs, json.dumps([i.model_dump() for i in sec_issues], indent=2))
        raw = client.complete_json(s, u, expected_type=(list, dict))
        return _parse_questions(raw), _parse_deleted_ids(raw)

    revised: list[SurveyQuestion] = []
    deleted_ids: list[str] = []
    groups = list(issues_by_section.items())
    with ThreadPoolExecutor(max_workers=min(REVIEW_MAX_WORKERS, max(1, len(groups)))) as ex:
        futures = [ex.submit(_revise, sec, iss) for sec, iss in groups]
        for fut in as_completed(futures):
            try:
                part_revised, part_deleted = fut.result()
            except Exception:  # noqa: BLE001 — a failed group shouldn't sink the whole revision
                continue
            revised.extend(part_revised)
            deleted_ids.extend(part_deleted)
    return revised, list(dict.fromkeys(deleted_ids))


def _validator_issues(report, include_warnings: bool = True) -> list[Issue]:
    """Turn the deterministic validator's findings into Issue objects the reviser can act on.
    This is what makes the validator a first-class issue source instead of a terminal report.
    Survey-level findings with no q_id (e.g. overall LOI) are skipped here — there is no single
    question to revise — and remain advisory. Uncovered-hypothesis findings are handled
    separately via _deterministic_missing_question_issues (they insert new questions)."""
    out: list[Issue] = []
    for idx, row in enumerate(report.issue_rows):
        sev = row.get("severity")
        if sev != "blocker" and not (include_warnings and sev in ("warning", "burden")):
            continue
        qid = str(row.get("q_id", "") or "").strip()
        if not qid:
            continue
        out.append(Issue(
            issue_id=f"DET_{row.get('issue_type', '')}_{idx}",
            q_id=qid,
            severity=_VALIDATOR_SEVERITY.get(sev, "minor"),
            issue_type=str(row.get("issue_type", "") or ""),
            description=str(row.get("message", "") or ""),
            fix_recommendation=str(row.get("recommended_fix", "") or ""),
            source_agent="deterministic_validator",
        ))
    return out


def _parse_editor_directives(data) -> tuple[list[Issue], str]:
    """Convert the holistic Survey Editor's directives into Issue objects for the reviser.
    A 'delete' becomes a delete instruction; a 'revise' carries the editor's instruction as
    the fix recommendation. Merges arrive pre-decomposed (one delete + one revise)."""
    if isinstance(data, dict):
        directives = data.get("directives", [])
        summary = str(data.get("summary", "") or "")
    elif isinstance(data, list):
        directives, summary = data, ""
    else:
        return [], ""
    out: list[Issue] = []
    for i, d in enumerate(directives if isinstance(directives, list) else []):
        if not isinstance(d, dict):
            continue
        qid = str(d.get("q_id", "") or "").strip()
        if not qid:
            continue
        action = str(d.get("action", "revise") or "revise").strip().lower()
        instruction = str(d.get("instruction", "") or "").strip()
        section_id = str(d.get("section_id", "") or "").strip()
        if action == "delete":
            issue_type = "editorial_delete"
            fix = (f"Delete this question entirely (redundant / low value): {instruction} "
                   "Put its q_id in deleted_question_ids; do not return it in revised_questions.")
        else:
            issue_type = "editorial_revise"
            fix = instruction or "Revise for whole-survey consistency."
        out.append(Issue(
            issue_id=f"EDIT_{i}",
            q_id=qid,
            severity="major",
            issue_type=issue_type,
            description=instruction or "Holistic survey-editor directive.",
            fix_recommendation=(f"{fix} (target section_id '{section_id}')" if section_id else fix),
            source_agent="survey_editor",
        ))
    return out, summary


def _run_survey_editor(client: LLMClient, study, questions: list[SurveyQuestion],
                       revision_list: list[Issue]) -> tuple[list[Issue], str]:
    """One holistic pass over the WHOLE survey. Reads everything, emits compact cross-survey
    directives (small output, no truncation), which the per-section executors then apply."""
    issues_json = json.dumps([i.model_dump() for i in revision_list], indent=2)
    s, u = prompts.survey_editor(study, questions, issues_json)
    data = client.complete_json(s, u, expected_type=(dict, list))
    return _parse_editor_directives(data)


def _sanitize_structural(questions: list[SurveyQuestion], sections: list[SectionProposal]) -> list[SurveyQuestion]:
    """Last-resort mechanical repair for residue the LLM couldn't fix, so the gate can always
    be cleared and the loop always terminates. Pure structural hygiene — no content invented:
      - a routing.next that resolves to neither a real q_id nor a terminal token is demoted to a
        programmer note and cleared (the human-readable intent is preserved, the blocker removed);
      - a RATING_* question with no statement rows gets one row synthesised from its own stem."""
    valid_ids = {q.q_id for q in questions if q.q_id}
    for q in questions:
        if q.routing and (q.routing.next or "").strip():
            target = q.routing.next.strip()
            if target.upper() not in _TERMINAL_ROUTES and target not in valid_ids:
                q.programming_instructions = list(q.programming_instructions or []) + [
                    f"ROUTING (needs manual wiring): {target}"
                ]
                q.routing.next = ""
        qtype = (q.question_type or "").strip().upper()
        if qtype in _RATING_TYPES and not q.options:
            stem = (q.question_text or "").strip()
            label = (stem[:117] + "...") if len(stem) > 120 else (stem or "Overall rating")
            q.options = [f"[1 | {label}]"]
    return questions


def _needs_delete(q: SurveyQuestion) -> bool:
    marker = (q.flag_status or "").strip().lower()
    text = (q.question_text or "").strip().upper()
    return marker in {"delete", "deleted", "remove", "removed"} or text.startswith("[DELETE]")


def _parse_deleted_ids(data) -> list[str]:
    if not isinstance(data, dict):
        return []
    out = []
    for key in ("deleted_question_ids", "delete_question_ids", "deleted_ids"):
        val = data.get(key, [])
        if isinstance(val, list):
            out += [str(x).strip() for x in val if str(x).strip()]
    return list(dict.fromkeys(out))


def _copy_section_defaults(q: SurveyQuestion, sections: list[SectionProposal]) -> SurveyQuestion:
    section_by_id = {s.section_id: s for s in sections}
    sec = section_by_id.get(q.section_id)
    if sec:
        q.section_id = sec.section_id
        q.section_title = q.section_title or sec.section_title
        q.section = q.section or sec.section_title
    return q


def _merge_revisions(existing: list[SurveyQuestion], revised: list[SurveyQuestion], issues: list[Issue],
                     sections: list[SectionProposal], deleted_ids: list[str] | None = None) -> list[SurveyQuestion]:
    deleted = set(deleted_ids or [])
    for q in revised:
        if _needs_delete(q):
            deleted.add(q.q_id)
    revised = [_copy_section_defaults(q, sections) for q in revised if not _needs_delete(q)]

    existing_ids = {q.q_id for q in existing}
    replace_by_id = {q.q_id: q for q in revised if q.q_id in existing_ids}
    new_questions = [q for q in revised if q.q_id not in existing_ids]

    merged: list[SurveyQuestion] = []
    for q in existing:
        if q.q_id in deleted:
            continue
        merged.append(replace_by_id.get(q.q_id, q))

    # Insert new questions after the last existing question in their section. If
    # the reviser omitted section_id, infer it from a missing_question issue.
    missing_issue_sections: list[str] = []
    for i in issues:
        m = re.search(r"section_id ['\"]?([A-Za-z0-9_-]+)['\"]?", i.fix_recommendation or "")
        if m:
            missing_issue_sections.append(m.group(1))
    default_new_section = missing_issue_sections[0] if missing_issue_sections else (sections[0].section_id if sections else "")
    section_title_by_id = {s.section_id: s.section_title for s in sections}
    for q in new_questions:
        if not q.section_id:
            q.section_id = default_new_section
        if not q.section_title:
            q.section_title = section_title_by_id.get(q.section_id, q.section)
        if not q.section:
            q.section = q.section_title
        insert_at = len(merged)
        for idx, existing_q in enumerate(merged):
            if existing_q.section_id == q.section_id:
                insert_at = idx + 1
        merged.insert(insert_at, q)
    return merged


def renumber_questions(questions: list[SurveyQuestion], sections: list[SectionProposal]) -> tuple[list[SurveyQuestion], dict[str, str]]:
    """Renumber questions deterministically in locked section order and repair routing targets."""
    section_order = {s.section_id: idx for idx, s in enumerate(sections)}
    section_title_by_id = {s.section_id: s.section_title for s in sections}
    ordered = sorted(
        questions,
        key=lambda q: (section_order.get(q.section_id, 9999), q.section_id or q.section, questions.index(q)),
    )
    counters: dict[str, int] = {}
    id_map: dict[str, str] = {}
    for q in ordered:
        if q.section_id in section_title_by_id:
            q.section_title = section_title_by_id[q.section_id]
            q.section = q.section_title
        sid = re.sub(r"[^A-Za-z0-9]+", "_", (q.section_id or q.section or "SEC")).strip("_").upper() or "SEC"
        counters[sid] = counters.get(sid, 0) + 1
        old_id = q.q_id
        q.q_id = f"{sid}_Q{counters[sid]:02d}"
        q.display_order = q.q_id
        if old_id:
            id_map[old_id] = q.q_id
    terminal_targets = {"END", "TERMINATE", "TERMINATION", "SUBMIT", "SCREENOUT", "SCREEN_OUT"}
    for q in ordered:
        if q.routing and q.routing.next:
            target = q.routing.next.strip()
            if target in id_map:
                q.routing.next = id_map[target]
            elif target.upper() in terminal_targets:
                q.routing.next = target.upper()
    return ordered, id_map


# ---- Steps 3-11 -------------------------------------------------------------
def run_pipeline_after_gate(client: LLMClient, study: StudySetup,
                            hyps: List[Hypothesis], sections: List[SectionProposal],
                            progress: ProgressFn = _noop, run_review: bool = True) -> Dict:
    hyps_by_id = {h.hypothesis_id: h for h in hyps}
    all_h_ids = [h.hypothesis_id for h in hyps]

    artifacts: dict = {
        "suggested_sections": [s.model_dump() for s in sections],
        "review_rounds": [],
        "revision_diffs": [],
        "renumber_map": {},
        "review_chunk_count": 1,
    }

    # Step 3 — Hypothesis Mapper
    progress("Hypothesis Mapper", "start", "Mapping each hypothesis to stable section_id + construct + type")
    s, u = prompts.hypothesis_mapper(study, hyps, sections)
    mappings = _model_list(client, s, u, HMapping, "Hypothesis Mapper", min_items=len(hyps))
    mappings = _normalise_mappings(mappings, sections)
    progress("Hypothesis Mapper", "done", f"{len(mappings)} mappings")

    # Step 4 — Pressure Tester
    progress("Pressure Tester", "start", "Adversarially challenging the mapping")
    s, u = prompts.pressure_tester(mappings, hyps, sections)
    try:
        revised_maps = _model_list(client, s, u, HMapping, "Pressure Tester", min_items=len(hyps))
        if revised_maps:
            mappings = _normalise_mappings(revised_maps, sections)
    except Exception as exc:  # noqa: BLE001 — enhancement step; keep original on failure
        progress("Pressure Tester", "info", f"Kept original mapping (correction unavailable: {exc})")
    mappings = _require_valid_mappings(mappings, hyps, sections)
    artifacts["mappings"] = [m.model_dump(by_alias=True) for m in mappings]
    progress("Pressure Tester", "done", f"{len(mappings)} validated mappings")

    # Step 5 — Question Drafter (one call per section, in parallel)
    progress("Question Drafter", "start", "Drafting questions per section (parallel)")
    section_ids_in_order = [s_.section_id for s_ in sections]
    section_titles_in_order = [s_.section_title for s_ in sections]
    maps_by_section_id: Dict[str, List[HMapping]] = {}
    for m in mappings:
        maps_by_section_id.setdefault(m.section_id, []).append(m)

    def _draft(section: SectionProposal) -> List[SurveyQuestion]:
        sec_maps = maps_by_section_id.get(section.section_id, [])
        sys_p, usr_p = prompts.question_drafter(study, section, sec_maps, hyps_by_id)
        questions = _model_list(client, sys_p, usr_p, SurveyQuestion, f"Question Drafter {section.section_id}", min_items=1)
        for sq in questions:
            sq.section_id = sq.section_id or section.section_id
            sq.section_title = sq.section_title or section.section_title
            sq.section = sq.section or section.section_title
        return questions

    questions: List[SurveyQuestion] = []
    draft_errors: list[str] = []
    with ThreadPoolExecutor(max_workers=min(6, max(1, len(sections)))) as ex:
        futures = {ex.submit(_draft, s_): s_ for s_ in sections}
        drafted: Dict[str, List[SurveyQuestion]] = {}
        for fut in as_completed(futures):
            s_ = futures[fut]
            try:
                section_questions = fut.result()
                if not section_questions:
                    draft_errors.append(f"Section '{s_.section_title}' returned zero valid questions.")
                drafted[s_.section_id] = section_questions
            except Exception as e:  # noqa: BLE001
                draft_errors.append(f"Section '{s_.section_title}' failed: {e}")
                drafted[s_.section_id] = []
    for sid in section_ids_in_order:
        questions.extend(drafted.get(sid, []))
    missing_sections = [s_.section_title for s_ in sections if not drafted.get(s_.section_id)]
    if draft_errors or missing_sections:
        details = draft_errors or [f"Section '{title}' returned zero valid questions." for title in missing_sections]
        raise LLMError("Question drafting failed for one or more approved sections:\n" + "\n".join(f"- {d}" for d in details[:20]))
    questions, _trimmed = _cap_questions(questions, QUESTION_BUDGET)
    if _trimmed:
        progress("Question Drafter", "info",
                 f"Survey was very large; trimmed {_trimmed} lowest-priority questions "
                 f"(soft backstop ~{QUESTION_BUDGET}, coverage preserved)")
    questions, renumber_map = renumber_questions(questions, sections)
    artifacts["renumber_map_initial"] = renumber_map
    progress("Question Drafter", "done", f"{len(questions)} questions drafted")

    # Step 6 — Deterministic Validator
    progress("Deterministic Validator", "start", "Rule-based coverage / routing / type checks")
    report = validate(questions, mappings, all_h_ids, sections)
    deterministic_missing_issues = _deterministic_missing_question_issues(report, mappings, sections)
    artifacts["initial_validation"] = report.model_dump()
    progress("Deterministic Validator", "done",
             f"{report.status} — {len(report.uncovered_hypotheses)} uncovered, {len(report.warnings)} warnings")

    # ---- Quality pass (Steps 7-11): reviewers → consolidate → holistic edit → revise ----
    consolidated = ConsolidatedResult()
    domain_key = prompts.domain_expert_key(study)

    if run_review:
        progress("Review Loop", "info", "Quality round 1 of 1")
        issues, chunk_count = _collect_review_issues(client, questions, study, section_titles_in_order, progress, domain_key)
        artifacts["review_chunk_count"] = max(artifacts.get("review_chunk_count", 1), chunk_count)
        if deterministic_missing_issues:
            issues.extend(deterministic_missing_issues)

        progress("Consolidator", "start", "Merging + triaging issues (parallel by section)")
        consolidated = _consolidate_issues(client, issues, questions, section_ids_in_order)
        progress("Consolidator", "done",
                 f"{consolidated.major_issue_count} major; "
                 f"{len(consolidated.revision_list)} to revise, "
                 f"{len(consolidated.auto_fix_issues)} auto-fix, "
                 f"{len(consolidated.escalated_issues)} escalated")

        if consolidated.auto_fix_issues:
            for auto_issue in consolidated.auto_fix_issues:
                auto_issue.source_agent = auto_issue.source_agent or "auto_fix"
            consolidated.revision_list.extend(consolidated.auto_fix_issues)
            consolidated.auto_fix_issues = []

        # Holistic survey editor — one pass over the WHOLE survey, emitting compact cross-survey
        # directives the per-section executors apply alongside the local quality fixes.
        progress("Survey Editor", "start", "Optimising the whole survey (cross-section)")
        try:
            editor_issues, editor_summary = _run_survey_editor(client, study, questions, consolidated.revision_list)
        except Exception as exc:  # noqa: BLE001 — the editorial pass is best-effort, never fatal
            editor_issues, editor_summary = [], f"(skipped: {exc})"
        consolidated.revision_list.extend(editor_issues)
        artifacts["editor_summary"] = editor_summary
        progress("Survey Editor", "done", f"{len(editor_issues)} cross-survey directive(s)")

        artifacts["review_rounds"].append({
            "round": 1,
            "raw_issue_count": len(issues),
            "consolidated": consolidated.model_dump(),
            "chunk_count": chunk_count,
            "editor_directives": len(editor_issues),
        })

        if consolidated.revision_list:
            progress("Question Reviser", "start",
                     f"Applying {len(consolidated.revision_list)} fixes + directives (parallel by section)")
            revised, deleted_ids = _revise_questions(client, questions, consolidated.revision_list, section_ids_in_order)
            before_revision = [q.model_copy(deep=True) for q in questions]
            questions = _merge_revisions(questions, revised, consolidated.revision_list, sections, deleted_ids)
            questions, round_renumber_map = renumber_questions(questions, sections)
            artifacts["renumber_map"].update(round_renumber_map)
            diffs = question_diffs(before_revision, questions)
            artifacts["revision_diffs"].extend(diffs)
            progress("Question Reviser", "done", f"{len(revised)} questions revised/inserted; {len(diffs)} field changes")
        else:
            progress("Review Loop", "done", "No revision issues from the quality pass")

    # ---- Deterministic repair loop: the validator drives revision until the rules pass ----
    # Every validator finding (blockers AND warnings) becomes an Issue fed to the reviser; we
    # re-validate after each round and re-feed what remains, up to MAX_REPAIR_ROUNDS.
    artifacts["repair_rounds"] = []
    report = validate(questions, mappings, all_h_ids, sections)
    for repair_round in range(MAX_REPAIR_ROUNDS):
        det_issues = _validator_issues(report, include_warnings=True)
        det_issues += _deterministic_missing_question_issues(report, mappings, sections)
        if not det_issues:
            progress("Repair Loop", "done", f"Rules satisfied after {repair_round} repair round(s)")
            break
        progress("Repair Loop", "start",
                 f"Round {repair_round + 1}/{MAX_REPAIR_ROUNDS}: "
                 f"{len(report.blockers)} blocker(s), {len(report.warnings)} warning(s) → reviser")
        before_repair = [q.model_copy(deep=True) for q in questions]
        revised, deleted_ids = _revise_questions(client, questions, det_issues, section_ids_in_order)
        questions = _merge_revisions(questions, revised, det_issues, sections, deleted_ids)
        questions, repair_map = renumber_questions(questions, sections)
        artifacts["renumber_map"].update(repair_map)
        artifacts["revision_diffs"].extend(question_diffs(before_repair, questions))
        artifacts["repair_rounds"].append({
            "round": repair_round + 1,
            "issues_fed": len(det_issues),
            "revised": len(revised),
        })
        report = validate(questions, mappings, all_h_ids, sections)
    else:
        progress("Repair Loop", "info", f"Hit repair cap ({MAX_REPAIR_ROUNDS}); sanitising residue")

    # ---- Deterministic sanitize net: neutralise the residue the LLM couldn't fix ----
    questions = _sanitize_structural(questions, sections)
    questions, final_renumber_map = renumber_questions(questions, sections)
    artifacts["renumber_map"].update(final_renumber_map)

    escalated_ids = {i.q_id: i for i in consolidated.escalated_issues}
    for q in questions:
        if q.q_id in escalated_ids:
            q.flag_status = "flagged"
            q.flag_description = escalated_ids[q.q_id].description

    report = validate(questions, mappings, all_h_ids, sections)
    artifacts["final_validation"] = report.model_dump()
    artifacts["final_questions"] = [q.model_dump() for q in questions]

    return {
        "questions": questions,
        "mappings": mappings,
        "validation": report,
        "consolidated": consolidated,
        "rounds_run": (1 if run_review else 0) + len(artifacts.get("repair_rounds", [])),
        "artifacts": artifacts,
        "revision_diffs": artifacts.get("revision_diffs", []),
        "question_id_map": artifacts.get("renumber_map", {}),
        "llm_usage": client.usage_snapshot() if hasattr(client, "usage_snapshot") else {},
    }


# ---- safe parsers -----------------------------------------------------------
def _parse_issues(data) -> List[Issue]:
    out = []
    if not isinstance(data, list):
        data = data.get("issues", []) if isinstance(data, dict) else []
    for d in data:
        try:
            out.append(Issue.model_validate(d))
        except ValidationError:
            continue
    return out


def _parse_questions(data) -> List[SurveyQuestion]:
    out = []
    if isinstance(data, dict):
        combined = []
        for key in ("questions", "revised_questions", "inserted_questions"):
            value = data.get(key, [])
            if isinstance(value, list):
                combined.extend(value)
        data = combined
    for d in data or []:
        # Some agents may return {"insert_after_q_id": "...", "question": {...}}.
        if isinstance(d, dict) and "question" in d and isinstance(d["question"], dict):
            d = d["question"]
        try:
            out.append(SurveyQuestion.model_validate(d))
        except ValidationError:
            continue
    return out


def _parse_consolidated(data) -> ConsolidatedResult:
    # Some models return a bare array of issues instead of the
    # {"revision_list": [...], ...} object. Treat that as the revision list.
    if isinstance(data, list):
        return ConsolidatedResult(revision_list=_parse_issues(data))
    if not isinstance(data, dict):
        return ConsolidatedResult()
    try:
        result = ConsolidatedResult.model_validate(data)
    except ValidationError:
        def _issues(key):
            return _parse_issues(data.get(key, []))
        result = ConsolidatedResult(
            revision_list=_issues("revision_list"),
            escalated_issues=_issues("escalated_issues"),
            auto_fix_issues=_issues("auto_fix_issues"),
            major_issue_count=int(data.get("major_issue_count", 0) or 0),
        )
    return result
