"""Deterministic question numbering and routing-target remapping."""
from __future__ import annotations

import re
from collections import defaultdict

from .models import SectionProposal, SurveyQuestion


def _prefix(section_id: str, fallback: str) -> str:
    raw = section_id or fallback or "SEC"
    clean = re.sub(r"[^A-Za-z0-9]+", "_", raw).strip("_").upper()
    return clean or "SEC"


def renumber_questions(questions: list[SurveyQuestion], sections: list[SectionProposal]) -> tuple[list[SurveyQuestion], dict[str, str]]:
    """Return questions sorted by section order and deterministically renumbered.

    Existing routing destinations are remapped if they referenced an old question
    ID. Terminal route tokens are preserved.
    """
    section_order = {s.section_id: i for i, s in enumerate(sections)}
    section_titles = {s.section_id: s.section_title for s in sections}
    ordered = sorted(
        [q.model_copy(deep=True) for q in questions],
        key=lambda q: (section_order.get(q.section_id, 10_000), q.section_id or q.section, q.q_id),
    )

    counters: dict[str, int] = defaultdict(int)
    used: set[str] = set()
    id_map: dict[str, str] = {}
    for q in ordered:
        sid = q.section_id or q.section or "SEC"
        counters[sid] += 1
        base = f"{_prefix(sid, q.section)}_Q{counters[sid]:02d}"
        new_id = base
        suffix = 2
        while new_id in used:
            new_id = f"{base}_{suffix}"
            suffix += 1
        old_id = q.q_id
        q.q_id = new_id
        used.add(new_id)
        if old_id:
            id_map[old_id] = new_id
        if q.section_id in section_titles:
            q.section_title = section_titles[q.section_id]
            q.section = section_titles[q.section_id]

    terminal = {"END", "TERMINATE", "TERMINATION", "SUBMIT", "COMPLETE", "SCREENOUT"}
    for q in ordered:
        if q.routing and q.routing.next:
            target = q.routing.next.strip()
            if target.upper() not in terminal and target in id_map:
                q.routing.next = id_map[target]
    return ordered, id_map
