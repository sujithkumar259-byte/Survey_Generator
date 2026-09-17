"""
Phase 2 — agent prompts (loader).

The instruction text for every LLM call lives in editable .txt files in the
prompts/ folder (one per step). This module loads those files, strips comments,
splits the `### SYSTEM` / `### USER` sections, and fills in the dynamic data
(study, hypotheses, sections, etc.). Editing a .txt file changes the prompt with
no code change; the data-injection placeholders must stay intact.

Public functions return (system, user) exactly as before, so the pipeline is
unchanged.
"""
from __future__ import annotations

import json
import os
from functools import lru_cache
from typing import List, Tuple

from survey_generator.contract import SUPPORTED_QUESTION_TYPES
from survey_generator.phase2.models import (
    Hypothesis, StudySetup, SectionProposal, HMapping, SurveyQuestion,
)

QTYPES = ", ".join(SUPPORTED_QUESTION_TYPES)

# prompts/ sits next to the survey_generator package root (../prompts from llm/)
PROMPTS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "prompts")

PROMPT_FILES = {
    "p1_clarifier": "p1_clarifier.txt",
    "p1_generator": "p1_generator.txt",
    "section_suggester": "01_section_suggester.txt",
    "hypothesis_mapper": "03_hypothesis_mapper.txt",
    "pressure_tester": "04_pressure_tester.txt",
    "question_drafter": "05_question_drafter.txt",
    "survey_flow_checker": "08_survey_flow_checker.txt",
    "quality_reviewer": "09_quality_reviewer.txt",
    "final_consolidator": "10_final_consolidator.txt",
    "survey_editor": "12_survey_editor.txt",
    "question_reviser": "11_question_reviser.txt",
}

# Study-type-specific domain-expert prompts live in prompts/domain_experts/.
# The file is chosen by normalising the study_type to a filename; if none
# matches, generic.txt is used.
DOMAIN_EXPERTS_DIR = os.path.join(PROMPTS_DIR, "domain_experts")

# Study-type-specific CREATION guidance (best practices) injected into the
# section-suggester and question-drafter steps. Chosen the same way as the
# domain expert; if no file exists for the study type, no guidance is added.
BEST_PRACTICES_DIR = os.path.join(PROMPTS_DIR, "best_practices")


# ---- file loading / parsing -------------------------------------------------
def _read(path: str) -> str:
    with open(path, encoding="utf-8") as f:
        return f.read()


@lru_cache(maxsize=1)
def _shared_system_context() -> str:
    return _read(os.path.join(PROMPTS_DIR, "00_shared_system_context.txt")).strip()


def _strip_comments(text: str) -> str:
    out = []
    for ln in text.splitlines():
        stripped = ln.lstrip()
        # keep '### SECTION' markers; drop only true comment lines
        if stripped.startswith("###"):
            out.append(ln)
        elif stripped.startswith("#"):
            continue
        else:
            out.append(ln)
    return "\n".join(out)


def _split_sections(raw: str) -> dict:
    """
    Split a template into its '### NAME' sections. Returns {name_lower: body}.
    Comment lines (#) are removed first.
    """
    raw = _strip_comments(raw)
    sections: dict = {}
    current = None
    buf: List[str] = []
    for line in raw.splitlines():
        if line.strip().startswith("### "):
            if current is not None:
                sections[current] = "\n".join(buf).strip()
            current = line.strip()[4:].strip().lower()
            buf = []
        else:
            buf.append(line)
    if current is not None:
        sections[current] = "\n".join(buf).strip()
    return sections


@lru_cache(maxsize=32)
def _template(key: str) -> dict:
    raw = _read(os.path.join(PROMPTS_DIR, PROMPT_FILES[key]))
    return _split_sections(raw)


def _fill(text: str, **values) -> str:
    """
    Fill {placeholders}. Literal braces in JSON examples are written as {{ }} in
    the templates, so str.format renders them as single braces.
    """
    values.setdefault("shared_system_context", _shared_system_context())
    values.setdefault("qtypes", QTYPES)
    return text.format(**values)


def _render(key: str, **values) -> Tuple[str, str]:
    tpl = _template(key)
    system = _fill(tpl.get("system", "{shared_system_context}"), **values)
    user = _fill(tpl.get("user", ""), **values)
    return system, user


# ---- data block helpers -----------------------------------------------------
def _hyp_block(hyps: List[Hypothesis]) -> str:
    return json.dumps([h.model_dump() for h in hyps], indent=2)


def _study_block(study: StudySetup) -> str:
    return json.dumps(study.model_dump(), indent=2)


@lru_cache(maxsize=32)
def _best_practices_text(key: str) -> str:
    path = os.path.join(BEST_PRACTICES_DIR, f"{key}.txt")
    if os.path.exists(path):
        return _strip_comments(_read(path)).strip()
    return ""


def _best_practices_block(study: StudySetup) -> str:
    """Creation-time best-practices guidance, injected into the section-suggester
    and question-drafter prompts.

    Always includes the cross-cutting `_common` rules (structure, numeric volumes,
    treatment-share allocation grid), then appends the study-type-specific file
    (e.g. Segmentation -> segmentation.txt) when one exists.
    """
    common = _best_practices_text("_common")
    specific = _best_practices_text(_normalise_study_type(study.study_type))
    length = _target_length_block(study)
    return "\n\n".join(block for block in (length, common, specific) if block)


def _target_length_block(study: StudySetup) -> str:
    """Turn the free-text target length (e.g. '30 minutes') into a sizing directive so
    the section-suggester and question-drafter size the survey to the requested LOI.
    Empty when the user leaves the length blank (behaviour is then unchanged)."""
    target = (getattr(study, "target_length", "") or "").strip()
    if not target:
        return ""
    return (
        "TARGET SURVEY LENGTH\n"
        f"- The full survey, INCLUDING the screener, must fit a target length of about {target}.\n"
        "- Size the number of sections and questions to hit that target — do not pad a short "
        "survey or over-trim a long one.\n"
        "- As a planning guide, a respondent completes roughly 2-3 well-designed questions per "
        "minute (grids, rankings and allocations count as more). Plan the total question count "
        "accordingly, then prioritise the highest-value questions for the hypotheses."
    )


# ---- Phase 1: Clarifier -----------------------------------------------------
def p1_clarifier(brief_block: str, frameworks: str, files_block: str):
    return _render("p1_clarifier",
                   brief_block=brief_block,
                   frameworks=frameworks,
                   files_block=files_block)


# ---- Phase 1: Hypothesis Generator ------------------------------------------
def p1_generator(brief_block: str, study_type: str, frameworks: str,
                 clarifications: str, files_block: str):
    return _render("p1_generator",
                   brief_block=brief_block,
                   study_type=study_type,
                   frameworks=frameworks,
                   clarifications=clarifications,
                   files_block=files_block)


# ---- Step 1: Section Suggester ----------------------------------------------
def section_suggester(study: StudySetup, hyps: List[Hypothesis]):
    return _render("section_suggester",
                   study_block=_study_block(study),
                   hyp_block=_hyp_block(hyps),
                   best_practices=_best_practices_block(study))


# ---- Step 3: Hypothesis Mapper ----------------------------------------------
def hypothesis_mapper(study: StudySetup, hyps: List[Hypothesis], sections: List[SectionProposal]):
    return _render("hypothesis_mapper",
                   sec_json=json.dumps([s.model_dump() for s in sections], indent=2),
                   hyp_block=_hyp_block(hyps))


# ---- Step 4: Pressure Tester ------------------------------------------------
def pressure_tester(mappings: List[HMapping], hyps: List[Hypothesis], sections: List[SectionProposal]):
    return _render("pressure_tester",
                   map_json=json.dumps([m.model_dump(by_alias=True) for m in mappings], indent=2),
                   sec_json=json.dumps([s.model_dump() for s in sections], indent=2))


# ---- Step 5: Question Drafter (one section at a time) -----------------------
def question_drafter(study: StudySetup, section: SectionProposal,
                     section_mappings: List[HMapping], hyps_by_id: dict):
    blueprint = []
    for m in section_mappings:
        h = hyps_by_id.get(m.hypothesis_id)
        blueprint.append({
            "hypothesis_id": m.hypothesis_id,
            "hypothesis_text": h.text if h else "",
            "section_id": m.section_id,
            "section_title": m.section_title or m.section,
            "construct": m.construct,
            "question_type": m.question_type,
        })
    return _render("question_drafter",
                   section_title=section.section_title,
                   section_id=section.section_id,
                   study_type=study.study_type,
                   disease_area=study.disease_area,
                   bp_json=json.dumps(blueprint, indent=2),
                   best_practices=_best_practices_block(study))


# ---- Step 7: Domain Expert (study-type specific) ----------------------------
def _normalise_study_type(study_type: str) -> str:
    """Map a free-text study type to a domain-expert key.

    Recognises common synonyms; falls back to a slug of the raw text, then to
    'generic' if no matching file exists.
    """
    t = (study_type or "").strip().lower()
    synonyms = {
        "segmentation": "segmentation",
        "segment": "segmentation",
        "atu": "atu",
        "a&u": "atu",
        "awareness": "atu",
        "awareness, trial & usage": "atu",
        "awareness trial usage": "atu",
        "tracking": "atu",
        "conjoint": "conjoint",
        "dce": "conjoint",
        "discrete choice": "conjoint",
        "maxdiff": "conjoint",
        "max-diff": "conjoint",
        "trade-off": "conjoint",
        "tradeoff": "conjoint",
        "dtc": "dtc",
        "patient": "dtc",
        "consumer": "dtc",
        "direct-to-consumer": "dtc",
    }
    for key, target in synonyms.items():
        if key in t:
            return target
    # try a direct slug match against an existing file
    slug = "".join(c if c.isalnum() else "_" for c in t).strip("_")
    if slug and os.path.exists(os.path.join(DOMAIN_EXPERTS_DIR, f"{slug}.txt")):
        return slug
    return "generic"


def domain_expert_key(study: StudySetup) -> str:
    """Public: which domain-expert prompt will be used for this study."""
    return _normalise_study_type(study.study_type)


@lru_cache(maxsize=32)
def _domain_template(key: str) -> dict:
    path = os.path.join(DOMAIN_EXPERTS_DIR, f"{key}.txt")
    if not os.path.exists(path):
        path = os.path.join(DOMAIN_EXPERTS_DIR, "generic.txt")
    return _split_sections(_read(path))


def conditional_domain_expert(questions: List[SurveyQuestion], study: StudySetup, trigger: str = ""):
    """Run the domain expert whose prompt matches the study type.

    `trigger` is accepted for backwards-compatibility but the file is chosen from
    the study type. If a caller passes an explicit trigger that names a file, it
    wins.
    """
    key = trigger.strip().lower() if trigger else ""
    if not key or not os.path.exists(os.path.join(DOMAIN_EXPERTS_DIR, f"{key}.txt")):
        key = _normalise_study_type(study.study_type)
    tpl = _domain_template(key)
    q_json = json.dumps([q.model_dump() for q in questions], indent=2)
    system = _fill(tpl.get("system", "{shared_system_context}"),
                   study_type=study.study_type, q_json=q_json)
    user = _fill(tpl.get("user", ""), study_type=study.study_type, q_json=q_json)
    return system, user



# ---- Step 8: Survey Flow Checker --------------------------------------------
def survey_flow_checker(questions: List[SurveyQuestion], section_order: List[str]):
    q_json = json.dumps([{"q_id": q.q_id, "section": q.section,
                          "question_type": q.question_type,
                          "question_text": q.question_text} for q in questions], indent=2)
    return _render("survey_flow_checker",
                   section_order=json.dumps(section_order),
                   q_json=q_json)


# ---- Step 9: Quality Reviewer (flags only) ----------------------------------
def quality_reviewer(questions: List[SurveyQuestion]):
    q_json = json.dumps([{"q_id": q.q_id, "question_type": q.question_type,
                          "question_text": q.question_text,
                          "options": q.options} for q in questions], indent=2)
    return _render("quality_reviewer", q_json=q_json)


# ---- Step 10: Final Consolidator --------------------------------------------
def final_consolidator(all_issues_json: str):
    return _render("final_consolidator", all_issues_json=all_issues_json)


# ---- Step 10b: Holistic Survey Editor ---------------------------------------
def survey_editor(study: StudySetup, questions: List[SurveyQuestion], issues_json: str):
    q_json = json.dumps([q.model_dump() for q in questions], indent=2)
    return _render("survey_editor", study_type=study.study_type,
                   q_json=q_json, issues_json=issues_json)


# ---- Step 11: Question Reviser ----------------------------------------------
def question_reviser(flagged_questions: List[SurveyQuestion], issues_json: str):
    q_json = json.dumps([q.model_dump() for q in flagged_questions], indent=2)
    return _render("question_reviser", q_json=q_json, issues_json=issues_json)


# ---- introspection (list prompt files; used by tooling, not the dashboard) --
def list_prompt_files() -> List[Tuple[str, str]]:
    """Return [(label, absolute_path)] for every editable prompt file."""
    out = [("Shared system context", os.path.join(PROMPTS_DIR, "00_shared_system_context.txt"))]
    for key, fname in PROMPT_FILES.items():
        out.append((fname, os.path.join(PROMPTS_DIR, fname)))
    if os.path.isdir(DOMAIN_EXPERTS_DIR):
        for fname in sorted(os.listdir(DOMAIN_EXPERTS_DIR)):
            if fname.endswith(".txt"):
                out.append((f"domain_experts/{fname}",
                            os.path.join(DOMAIN_EXPERTS_DIR, fname)))
    return out
