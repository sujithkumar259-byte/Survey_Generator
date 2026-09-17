"""Deterministic survey length and respondent-burden heuristics."""
from __future__ import annotations

from collections import Counter
from typing import Iterable

from .models import SurveyQuestion

# Conservative production defaults. These are intentionally transparent and can
# be tuned by methodology leads without touching the LLM prompts.
BASE_SECONDS_BY_TYPE = {
    "SINGLE_SELECT": 15,
    "MULTI_SELECT": 20,
    "NUMERIC": 15,
    "OPEN_END": 60,
    "RANKING": 45,
    "PERCENT_ALLOCATION": 50,
    "AWARENESS_USAGE": 35,
    "DROPDOWN": 30,
    "GRID": 20,
    "TPP_DISPLAY": 20,
    "INSTRUCTION": 8,
    "RATING_DUAL": 35,
    "RATING_7": 25,
    "RATING_7_IMPORTANCE": 25,
    "RATING_7_SATISFACTION": 25,
    "RATING_7_LIKELIHOOD": 25,
    "RATING_7_FAMILIARITY": 25,
}
PER_ITEM_SECONDS_BY_TYPE = {
    "GRID": 8,
    "DROPDOWN": 8,
    "RATING_7": 7,
    "RATING_7_IMPORTANCE": 7,
    "RATING_7_SATISFACTION": 7,
    "RATING_7_LIKELIHOOD": 7,
    "RATING_7_FAMILIARITY": 7,
    "RATING_DUAL": 8,
    "AWARENESS_USAGE": 7,
    "PERCENT_ALLOCATION": 8,
    "RANKING": 4,
    "MULTI_SELECT": 2,
}


def estimate_question_seconds(q: SurveyQuestion) -> int:
    base = BASE_SECONDS_BY_TYPE.get(q.question_type, 20)
    item_count = len(q.options or [])
    if q.question_type == "RATING_DUAL":
        item_count = max(item_count, 1)
    if q.question_type in {"GRID", "DROPDOWN"}:
        item_count = max(len(q.options or []), 1)
    per_item = PER_ITEM_SECONDS_BY_TYPE.get(q.question_type, 0)
    return int(base + per_item * item_count)


def estimate_survey_length_minutes(questions: Iterable[SurveyQuestion]) -> float:
    total_seconds = sum(estimate_question_seconds(q) for q in questions)
    return round(total_seconds / 60.0, 1)


def respondent_burden_warnings(questions: list[SurveyQuestion]) -> list[str]:
    warnings: list[str] = []
    counts = Counter(q.question_type for q in questions)
    grid_like = counts["GRID"] + counts["DROPDOWN"] + counts["AWARENESS_USAGE"]
    rating_like = sum(counts[t] for t in (
        "RATING_7", "RATING_7_IMPORTANCE", "RATING_7_SATISFACTION",
        "RATING_7_LIKELIHOOD", "RATING_7_FAMILIARITY", "RATING_DUAL",
    ))
    open_ends = counts["OPEN_END"]
    rankings = counts["RANKING"]
    length = estimate_survey_length_minutes(questions)
    if length > 30:
        warnings.append(f"Estimated LOI is {length:.1f} minutes, above the 30-minute review threshold.")
    if grid_like > 6:
        warnings.append(f"Survey contains {grid_like} grid/dropdown/awareness batteries; consider reducing respondent burden.")
    if rating_like > 10:
        warnings.append(f"Survey contains {rating_like} rating batteries; check for repetitive batteries.")
    if open_ends > 3:
        warnings.append(f"Survey contains {open_ends} open-end questions; consider reducing free-text burden.")
    if rankings > 2:
        warnings.append(f"Survey contains {rankings} ranking tasks; consider limiting ranking exercises.")
    for q in questions:
        option_count = len(q.options or [])
        if q.question_type in {"SINGLE_SELECT", "MULTI_SELECT", "RANKING"} and option_count > 12:
            warnings.append(f"{q.q_id} has {option_count} options; long option lists can increase fatigue.")
        if q.question_type in {"GRID", "DROPDOWN", "AWARENESS_USAGE"} and option_count > 12:
            warnings.append(f"{q.q_id} has {option_count} rows; consider splitting or shortening the grid.")
    return list(dict.fromkeys(warnings))
