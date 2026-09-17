"""
Phase 2 — data models (the spine).

Pydantic models for every artifact passed between agents. The question model's
question_type is constrained to the 17 types the Phase 3 renderer supports, so
an invalid type fails fast at the boundary rather than silently producing an
un-renderable spec.
"""
from __future__ import annotations

from typing import Any, List, Optional, Literal
from pydantic import BaseModel, Field, model_validator

QuestionType = Literal[
    "RATING_7", "RATING_7_IMPORTANCE", "RATING_7_SATISFACTION",
    "RATING_7_LIKELIHOOD", "RATING_7_FAMILIARITY", "RATING_DUAL",
    "SINGLE_SELECT", "MULTI_SELECT", "NUMERIC", "PERCENT_ALLOCATION",
    "RANKING", "AWARENESS_USAGE", "DROPDOWN", "OPEN_END", "GRID",
    "TPP_DISPLAY", "INSTRUCTION",
]


# ---- Phase 1 outputs (inputs to Phase 2) ------------------------------------
class Hypothesis(BaseModel):
    hypothesis_id: str
    text: str
    category: str = ""          # construct/dimension category from Phase 1
    rationale: str = ""         # how this hypothesis serves the study type
    target_segment: str = ""    # unused in Phase 1; kept for back-compat
    study_type: str = ""

    # Phase 1 production-readiness metadata. Phase 2 can ignore these, but
    # preserving them lets users trace/edit/approve hypotheses before handoff.
    business_question: str = ""
    audience: str = ""
    priority: str = "Medium"    # High | Medium | Low
    status: str = "Approved"    # Approved | Needs Edit | Rejected
    notes: str = ""
    source_filenames: List[str] = Field(default_factory=list)
    source_refs: List[str] = Field(default_factory=list)
    source_excerpt: str = ""
    validation_warnings: List[str] = Field(default_factory=list)
    duplicate_of: str = ""

    # Backwards-compatible aliases used by earlier prototypes.
    review_status: str = ""
    review_notes: str = ""
    source_files: List[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _populate_phase1_aliases(self):
        if not self.status and self.review_status:
            self.status = self.review_status
        if not self.review_status and self.status:
            self.review_status = self.status
        if not self.notes and self.review_notes:
            self.notes = self.review_notes
        if not self.review_notes and self.notes:
            self.review_notes = self.notes
        if not self.source_filenames and self.source_files:
            self.source_filenames = self.source_files
        if not self.source_files and self.source_filenames:
            self.source_files = self.source_filenames
        return self


class StudySetup(BaseModel):
    study_type: str
    target_population: str
    disease_area: str = ""
    frameworks: List[str] = Field(default_factory=list)
    user_nuance: str = ""
    target_length: str = ""          # target LOI incl. screener, free text e.g. "30 minutes"
    client_name: str = "Demo Client"
    study_title: str = "Untitled Study"


# ---- Step 1/2: sections -----------------------------------------------------
class SectionProposal(BaseModel):
    section_id: str
    section_title: str
    rationale: str = ""
    is_must_have: bool = False


# ---- Step 3/4: hypothesis -> section mapping --------------------------------
class HMapping(BaseModel):
    model_config = {"populate_by_name": True}
    hypothesis_id: str
    section_id: str = ""          # stable section key used by the pipeline
    section_title: str = ""       # human-readable section title
    section: str = ""             # legacy title field; kept for older prompts
    construct_: str = Field(alias="construct")
    question_type: QuestionType
    priority: str = "primary"

    @model_validator(mode="after")
    def _clean_section_fields(self):
        self.section_id = (self.section_id or "").strip()
        self.section_title = (self.section_title or "").strip()
        self.section = (self.section or "").strip()
        if not self.section_title and self.section:
            self.section_title = self.section
        if not self.section and self.section_title:
            self.section = self.section_title
        return self

    @property
    def construct(self) -> str:
        return self.construct_


# ---- Step 5/11: questions ---------------------------------------------------
class ScaleSpec(BaseModel):
    min: int = 1
    max: int = 7
    anchor_low: str = ""
    anchor_high: str = ""


class RoutingSpec(BaseModel):
    condition: str = "all respondents"
    next: str = ""


class SurveyQuestion(BaseModel):
    q_id: str
    section: str
    section_id: str = ""
    section_title: str = ""
    display_order: str = ""
    hypothesis_ids: List[str] = Field(default_factory=list)
    question_text: str
    question_type: QuestionType
    options: List[str] = Field(default_factory=list)   # raw "[code | label]" lines
    grid_columns: List[str] = Field(default_factory=list)
    dropdown_options: List[str] = Field(default_factory=list)
    scale: Optional[ScaleSpec] = None
    routing: Optional[RoutingSpec] = None
    termination_logic: List[str] = Field(default_factory=list)
    option_level_logic: List[str] = Field(default_factory=list)
    programming_instructions: List[str] = Field(default_factory=list)
    section_objective: str = ""
    interviewer_notes: str = ""
    notes: str = ""
    # populated by the reviser/consolidator
    flag_status: str = "clean"          # clean | flagged | delete
    flag_description: str = ""

    @model_validator(mode="after")
    def _clean_aliases(self):
        if not self.section_title and self.section:
            self.section_title = self.section
        if not self.section and self.section_title:
            self.section = self.section_title
        if not self.display_order and self.q_id:
            self.display_order = self.q_id
        return self


# ---- Steps 7/8/9/10: issues -------------------------------------------------
Severity = Literal["minor", "major", "critical"]


class Issue(BaseModel):
    issue_id: str = ""
    q_id: str = ""
    severity: Severity = "minor"
    issue_type: str = ""
    description: str = ""
    fix_recommendation: str = ""
    source_agent: str = ""


class ConsolidatedResult(BaseModel):
    revision_list: List[Issue] = Field(default_factory=list)   # send to reviser
    escalated_issues: List[Issue] = Field(default_factory=list)  # human review
    auto_fix_issues: List[Issue] = Field(default_factory=list)
    major_issue_count: int = 0


# ---- Step 6: deterministic validation ---------------------------------------
class ValidationReport(BaseModel):
    passed: bool = True
    status: str = "PASS"  # PASS | WARNING | BLOCKER
    can_export: bool = True
    blockers: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    burden_warnings: List[str] = Field(default_factory=list)
    issue_rows: List[dict[str, Any]] = Field(default_factory=list)
    coverage_rows: List[dict[str, Any]] = Field(default_factory=list)
    uncovered_hypotheses: List[str] = Field(default_factory=list)
    coverage_map: dict = Field(default_factory=dict)   # hypothesis_id -> [q_id]
    question_count: int = 0
    estimated_length_seconds: int = 0
    estimated_length_minutes: float = 0.0
    loi_seconds: int = 0
    loi_minutes: float = 0.0
