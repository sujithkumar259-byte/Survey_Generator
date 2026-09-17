"""
Phase 2 deterministic validation.

This rule engine is intentionally stricter than the Phase 3 Word renderer. Phase
3 can render imperfect workbooks in draft mode; Phase 2 should prevent broken
survey logic from becoming an approved outline without an explicit override.
"""
from __future__ import annotations

from collections import Counter
from typing import Dict, List

from survey_generator.contract import SUPPORTED_QUESTION_TYPES

from .metrics import estimate_question_seconds, respondent_burden_warnings
from .models import HMapping, Hypothesis, SectionProposal, SurveyQuestion, ValidationReport

CONSTRUCT_QTYPE_RULES: Dict[str, List[str]] = {
    "importance": ["RATING_7_IMPORTANCE", "RATING_7", "RANKING", "MULTI_SELECT"],
    "satisfaction": ["RATING_7_SATISFACTION", "RATING_7"],
    "likelihood": ["RATING_7_LIKELIHOOD", "RATING_7"],
    "familiarity": ["RATING_7_FAMILIARITY", "RATING_7", "AWARENESS_USAGE"],
    "awareness": ["AWARENESS_USAGE", "MULTI_SELECT", "SINGLE_SELECT"],
    "usage": ["AWARENESS_USAGE", "SINGLE_SELECT", "MULTI_SELECT"],
    "agreement": ["RATING_7", "RATING_DUAL"],
    "attitude": ["RATING_7", "RATING_DUAL"],
    "preference": ["RATING_DUAL", "RANKING", "SINGLE_SELECT"],
    "ranking": ["RANKING"],
    "allocation": ["PERCENT_ALLOCATION"],
    "share": ["PERCENT_ALLOCATION", "NUMERIC"],
    "rating": ["RATING_7", "RATING_7_IMPORTANCE", "RATING_7_SATISFACTION", "RATING_7_LIKELIHOOD", "RATING_7_FAMILIARITY"],
    "open": ["OPEN_END"],
    "verbatim": ["OPEN_END"],
    "count": ["NUMERIC"],
    "number": ["NUMERIC"],
    "selection": ["SINGLE_SELECT", "MULTI_SELECT"],
    "screening": ["SINGLE_SELECT", "NUMERIC", "MULTI_SELECT"],
}

TERMINAL_ROUTING_TARGETS = {"END", "TERMINATE", "TERMINATION", "SUBMIT", "SCREENOUT", "SCREEN_OUT", "COMPLETE"}
RATING_TYPES = {"RATING_7", "RATING_7_IMPORTANCE", "RATING_7_SATISFACTION", "RATING_7_LIKELIHOOD", "RATING_7_FAMILIARITY"}


class MappingValidationError(ValueError):
    """Raised when the hypothesis-to-section mapping cannot be safely drafted."""


def normalize_mappings_to_sections(mappings: List[HMapping], sections: List[SectionProposal]) -> List[HMapping]:
    by_id = {s.section_id.strip(): s for s in sections if s.section_id.strip()}
    by_title = {s.section_title.strip().lower(): s for s in sections if s.section_title.strip()}
    normalized: list[HMapping] = []
    for mapping in mappings:
        m = mapping.model_copy(deep=True)
        section = by_id.get(m.section_id)
        if section is None and m.section:
            section = by_id.get(m.section.strip()) or by_title.get(m.section.strip().lower())
        if section is not None:
            m.section_id = section.section_id
            m.section = section.section_title
            m.section_title = section.section_title
        normalized.append(m)
    return normalized


def validate_mappings(mappings: List[HMapping], hypotheses: List[Hypothesis], sections: List[SectionProposal]) -> list[str]:
    blockers: list[str] = []
    hyp_ids = [h.hypothesis_id for h in hypotheses]
    hyp_id_set = set(hyp_ids)
    section_ids = {s.section_id for s in sections}
    seen: dict[str, int] = {}
    for m in mappings:
        if m.hypothesis_id not in hyp_id_set:
            blockers.append(f"Mapping references unknown hypothesis_id '{m.hypothesis_id}'.")
        if not m.section_id:
            blockers.append(f"Hypothesis {m.hypothesis_id} has no section_id.")
        elif m.section_id not in section_ids:
            blockers.append(f"Hypothesis {m.hypothesis_id} maps to unknown section_id '{m.section_id}'.")
        seen[m.hypothesis_id] = seen.get(m.hypothesis_id, 0) + 1
    for hid in hyp_ids:
        if seen.get(hid, 0) == 0:
            blockers.append(f"Hypothesis {hid} is missing from the section mapping.")
        elif seen.get(hid, 0) > 1:
            blockers.append(f"Hypothesis {hid} appears {seen[hid]} times in the section mapping.")
    return blockers


def assert_valid_mappings(mappings: List[HMapping], hypotheses: List[Hypothesis], sections: List[SectionProposal]) -> None:
    blockers = validate_mappings(mappings, hypotheses, sections)
    if blockers:
        preview = "; ".join(blockers[:8])
        if len(blockers) > 8:
            preview += f"; +{len(blockers) - 8} more"
        raise MappingValidationError(preview)


def _qtype_ok_for_construct(construct: str, qtype: str) -> bool:
    c = (construct or "").lower()
    matched_any_rule = False
    for key, allowed in CONSTRUCT_QTYPE_RULES.items():
        if key in c:
            matched_any_rule = True
            if qtype in allowed:
                return True
    return not matched_any_rule


def _append_issue(report: ValidationReport, severity: str, issue_type: str, q_id: str, message: str, fix: str = "") -> None:
    row = {
        "severity": severity,
        "issue_type": issue_type,
        "q_id": q_id,
        "message": message,
        "recommended_fix": fix,
        "status": "Open",
    }
    report.issue_rows.append(row)
    if severity == "blocker":
        report.blockers.append(message)
    elif severity == "burden":
        report.burden_warnings.append(message)
        report.warnings.append(message)
    else:
        report.warnings.append(message)


def _has_scale_anchors(q: SurveyQuestion) -> bool:
    return bool(q.scale and str(q.scale.anchor_low).strip() and str(q.scale.anchor_high).strip())


def _question_type_validation(q: SurveyQuestion, report: ValidationReport) -> None:
    qid = q.q_id or "[missing q_id]"
    qtype = (q.question_type or "").strip().upper()
    if not q.q_id.strip():
        _append_issue(report, "blocker", "missing_question_id", qid, "A question is missing q_id.", "Assign a unique question ID.")
    if not q.question_text.strip():
        _append_issue(report, "blocker", "missing_question_text", qid, f"{qid} has blank question_text.", "Add final question text.")
    if not (q.section_id or q.section).strip():
        _append_issue(report, "blocker", "missing_section", qid, f"{qid} has no section assignment.", "Assign this question to a valid section.")
    if qtype not in SUPPORTED_QUESTION_TYPES:
        _append_issue(report, "blocker", "unsupported_question_type", qid, f"{qid} uses unsupported question_type '{q.question_type}'.", "Choose a supported Phase 3 question type.")
        return
    if qtype in ("SINGLE_SELECT", "MULTI_SELECT") and len(q.options) < 2:
        _append_issue(report, "blocker", "missing_options", qid, f"{qid} {qtype} requires at least 2 options.", "Add at least two answer options.")
    if qtype in RATING_TYPES:
        if not q.options:
            _append_issue(report, "blocker", "missing_statements", qid, f"{qid} {qtype} requires at least one statement/row.", "Add rating statements in options.")
        if not q.scale:
            _append_issue(report, "warning", "missing_scale", qid, f"{qid} {qtype} has no scale object; default 1-7 will be used.", "Set scale min/max and anchors.")
        elif q.scale.min >= q.scale.max:
            _append_issue(report, "blocker", "invalid_scale", qid, f"{qid} has invalid scale min/max.", "Set scale min lower than scale max.")
        elif not _has_scale_anchors(q):
            _append_issue(report, "warning", "missing_scale_anchors", qid, f"{qid} is missing low/high scale anchors.", "Add anchor_low and anchor_high.")
    if qtype == "RATING_DUAL" and not q.options:
        _append_issue(report, "blocker", "missing_dual_pairs", qid, f"{qid} RATING_DUAL requires bipolar statement pairs.", "Add rows shaped like '[1 | Statement A vs Statement B]'.")
    if qtype == "GRID":
        if not q.options:
            _append_issue(report, "blocker", "missing_grid_rows", qid, f"{qid} GRID requires row statements/options.", "Add grid rows in options.")
        if not q.grid_columns:
            _append_issue(report, "blocker", "missing_grid_columns", qid, f"{qid} GRID requires grid_columns.", "Add grid column definitions.")
    if qtype == "DROPDOWN":
        if not q.options:
            _append_issue(report, "blocker", "missing_dropdown_rows", qid, f"{qid} DROPDOWN requires row statements/options.", "Add dropdown rows in options.")
        if not q.grid_columns:
            _append_issue(report, "blocker", "missing_dropdown_grid_columns", qid, f"{qid} DROPDOWN requires grid_columns.", "Add dropdown grid columns.")
        if not q.dropdown_options:
            _append_issue(report, "blocker", "missing_dropdown_options", qid, f"{qid} DROPDOWN requires dropdown_options.", "Add dropdown option list.")
    if qtype == "RANKING" and len(q.options) < 2:
        _append_issue(report, "blocker", "missing_ranking_items", qid, f"{qid} RANKING requires at least 2 rankable items.", "Add rankable items.")
    if qtype == "PERCENT_ALLOCATION" and len(q.options) < 2:
        _append_issue(report, "blocker", "missing_allocation_items", qid, f"{qid} PERCENT_ALLOCATION requires at least 2 allocation items.", "Add allocation items.")
    if qtype == "AWARENESS_USAGE" and len(q.options) < 1:
        _append_issue(report, "blocker", "missing_awareness_usage_items", qid, f"{qid} AWARENESS_USAGE requires product/treatment rows.", "Add product/treatment rows.")
    if qtype == "OPEN_END" and q.options:
        _append_issue(report, "warning", "open_end_has_options", qid, f"{qid} OPEN_END has coded options; confirm this is intended.", "Remove options or choose a structured question type.")


def _section_validation(q: SurveyQuestion, report: ValidationReport, sections: list[SectionProposal] | None) -> None:
    if not sections:
        return
    section_ids = {s.section_id for s in sections}
    section_titles = {s.section_title for s in sections}
    qid = q.q_id or "[missing q_id]"
    if q.section_id and q.section_id not in section_ids:
        _append_issue(report, "blocker", "invalid_section_id", qid, f"{qid} is assigned to unknown section_id '{q.section_id}'.", "Assign the question to a locked section_id.")
    elif not q.section_id and q.section not in section_titles:
        _append_issue(report, "blocker", "invalid_section", qid, f"{qid} is assigned to unknown section '{q.section}'.", "Assign the question to a locked section.")


def _coverage_rows(all_hypothesis_ids: list[str], coverage: dict[str, list[str]]) -> list[dict]:
    return [
        {
            "Hypothesis_ID": hid,
            "Covered_By": "; ".join(coverage.get(hid, [])),
            "Coverage_Status": "Covered" if coverage.get(hid) else "Not Covered",
        }
        for hid in all_hypothesis_ids
    ]


def validate(
    questions: List[SurveyQuestion],
    mappings: List[HMapping],
    all_hypothesis_ids: List[str],
    sections: List[SectionProposal] | None = None,
) -> ValidationReport:
    report = ValidationReport()
    report.question_count = len(questions)

    coverage: Dict[str, List[str]] = {h: [] for h in all_hypothesis_ids}
    for q in questions:
        for hid in q.hypothesis_ids:
            coverage.setdefault(hid, [])
            if q.q_id and q.q_id not in coverage[hid]:
                coverage[hid].append(q.q_id)
    report.coverage_map = coverage
    report.coverage_rows = _coverage_rows(all_hypothesis_ids, coverage)
    report.uncovered_hypotheses = [h for h, qs in coverage.items() if not qs and h in set(all_hypothesis_ids)]
    for h in report.uncovered_hypotheses:
        _append_issue(report, "blocker", "uncovered_hypothesis", "", f"Hypothesis {h} is not covered by any question.", "Add or map a question covering this hypothesis, or waive the hypothesis.")

    ids = [q.q_id.strip() for q in questions if q.q_id.strip()]
    id_counts = Counter(ids)
    for qid, count in id_counts.items():
        if count > 1:
            _append_issue(report, "blocker", "duplicate_question_id", qid, f"Duplicate q_id {qid} appears {count} times.", "Assign unique question IDs.")
    for q in questions:
        _question_type_validation(q, report)
        _section_validation(q, report, sections)

    display_counts = Counter((q.section_id or q.section, q.display_order) for q in questions if q.display_order)
    for (section_key, display_order), count in display_counts.items():
        if count > 1:
            _append_issue(report, "warning", "duplicate_display_order", "", f"Display_Order {display_order} appears {count} times in section {section_key}.", "Review ordering or let deterministic renumbering reset display_order.")

    q_ids = {q.q_id for q in questions if q.q_id}
    for q in questions:
        if q.routing and q.routing.next:
            target = q.routing.next.strip()
            if target and target.upper() not in TERMINAL_ROUTING_TARGETS and target not in q_ids:
                _append_issue(report, "blocker", "invalid_routing_target", q.q_id, f"{q.q_id} routes to unknown target '{target}'.", "Update Routing_Next to a valid Question_ID or terminal target.")

    construct_by_h: Dict[str, str] = {m.hypothesis_id: m.construct for m in mappings}
    for q in questions:
        constructs = [construct_by_h.get(h, "") for h in q.hypothesis_ids]
        constructs = [c for c in constructs if c]
        if constructs and not any(_qtype_ok_for_construct(c, q.question_type) for c in constructs):
            _append_issue(report, "warning", "construct_type_mismatch", q.q_id, f"{q.q_id} uses {q.question_type} but its construct(s) {constructs} suggest a different type.", "Confirm the question type or update the mapping construct.")

    seconds = int(sum(estimate_question_seconds(q) for q in questions))
    report.estimated_length_seconds = seconds
    report.estimated_length_minutes = round(seconds / 60.0, 1)
    report.loi_seconds = seconds
    report.loi_minutes = report.estimated_length_minutes
    for msg in respondent_burden_warnings(questions):
        _append_issue(report, "burden", "respondent_burden", "", msg, "Review survey length and respondent burden.")

    report.blockers = list(dict.fromkeys(report.blockers))
    report.warnings = list(dict.fromkeys(report.warnings))
    report.burden_warnings = list(dict.fromkeys(report.burden_warnings))
    # Stable de-dupe for issue rows.
    seen = set()
    rows = []
    for row in report.issue_rows:
        key = (row.get("severity"), row.get("issue_type"), row.get("q_id"), row.get("message"))
        if key not in seen:
            seen.add(key)
            rows.append(row)
    report.issue_rows = rows
    report.passed = len(report.blockers) == 0
    report.can_export = report.passed
    report.status = "BLOCKER" if report.blockers else ("WARNING" if report.warnings else "PASS")
    return report
