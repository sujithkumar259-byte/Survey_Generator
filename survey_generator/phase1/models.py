"""
Phase 1 — data models.

Phase 1 turns uploaded source files + analyst context into approved research
hypotheses for Phase 2. This module holds Phase-1-only inputs, clarifying
question structures, and extraction diagnostics.
"""
from __future__ import annotations

from typing import List
from pydantic import BaseModel, Field, model_validator
from survey_generator.runtime import sanitize_text, shorten_text
from .frameworks import FRAMEWORKS, CUSTOM_FRAMEWORK_NAME


class StudyBrief(BaseModel):
    goal: str = ""
    study_type: str = ""
    disease_area: str = ""
    target_audience: str = ""
    market: str = ""
    key_decisions: str = ""
    extra_context: str = ""
    survey_length: str = ""          # target LOI incl. screener, free text e.g. "30 minutes"
    frameworks: List[str] = Field(default_factory=list)
    custom_framework_prompt: str = ""
    client_name: str = "Demo Client"
    study_title: str = ""


class SourceDoc(BaseModel):
    """A parsed uploaded file plus extraction QA metadata.

    `text` is the bounded prompt text shown in preview and sent to the LLM.
    The field set intentionally supports both the original app names and the
    newer extraction-summary names so older saved session state still loads.
    """
    filename: str
    kind: str
    text: str = ""
    note: str = ""

    # File identity / size
    extension: str = ""
    ext: str = ""
    file_size_bytes: int = 0

    # Unit counts: pages/slides/sheets/rows/lines
    item_count: int = 0
    item_unit: str = "items"
    unit_count: int = 0
    unit_label: str = "items"
    total_units: int = 0
    processed_units: int = 0
    pages_with_text: int = 0

    # Character counts / truncation
    raw_char_count: int = 0
    extracted_char_count: int = 0
    raw_chars: int = 0
    extracted_chars: int = 0
    prompt_chars: int = 0
    was_truncated: bool = False
    truncated: bool = False
    truncation_note: str = ""

    # QA
    weak_extraction: bool = False
    weak_reason: str = ""
    warnings: List[str] = Field(default_factory=list)
    errors: List[str] = Field(default_factory=list)
    ocr_attempted: bool = False
    ocr_used: bool = False
    preview_text: str = ""

    @model_validator(mode="after")
    def _sync_aliases(self):
        # Sanitize extracted text and diagnostics loaded from current or older
        # session state. This prevents invisible PPTX/PDF control characters from
        # breaking later Excel exports or verbose UI errors.
        self.filename = sanitize_text(self.filename)
        self.kind = sanitize_text(self.kind)
        self.text = sanitize_text(self.text)
        self.note = shorten_text(self.note, 500, collapse_whitespace=False)
        self.extension = sanitize_text(self.extension)
        self.ext = sanitize_text(self.ext)
        self.preview_text = sanitize_text(self.preview_text)
        self.warnings = [shorten_text(w, 800, collapse_whitespace=True) for w in (self.warnings or [])]
        self.errors = [shorten_text(e, 800, collapse_whitespace=True) for e in (self.errors or [])]

        if self.extension and not self.ext:
            self.ext = self.extension
        if self.ext and not self.extension:
            self.extension = self.ext

        if self.item_count and not self.unit_count:
            self.unit_count = self.item_count
        if self.unit_count and not self.item_count:
            self.item_count = self.unit_count
        if self.item_unit and (not self.unit_label or self.unit_label == "items"):
            self.unit_label = self.item_unit
        if self.unit_label and (not self.item_unit or self.item_unit == "items"):
            self.item_unit = self.unit_label
        if self.item_count and not self.total_units:
            self.total_units = self.item_count
        if self.item_count and not self.processed_units:
            self.processed_units = self.item_count

        if self.raw_char_count and not self.raw_chars:
            self.raw_chars = self.raw_char_count
        if self.raw_chars and not self.raw_char_count:
            self.raw_char_count = self.raw_chars
        if self.extracted_char_count and not self.extracted_chars:
            self.extracted_chars = self.extracted_char_count
        if self.extracted_chars and not self.extracted_char_count:
            self.extracted_char_count = self.extracted_chars
        if self.prompt_chars and not self.extracted_char_count:
            self.extracted_char_count = self.prompt_chars
            self.extracted_chars = self.prompt_chars
        if not self.extracted_char_count:
            self.extracted_char_count = len(self.text or "")
            self.extracted_chars = self.extracted_char_count
            self.prompt_chars = self.prompt_chars or self.extracted_char_count
        if not self.raw_char_count:
            self.raw_char_count = len(self.text or "")
            self.raw_chars = self.raw_char_count

        if self.was_truncated and not self.truncated:
            self.truncated = True
        if self.truncated and not self.was_truncated:
            self.was_truncated = True
        if not self.preview_text:
            self.preview_text = (self.text or "")[:5000]
        return self

    @property
    def file_type(self) -> str:
        """Compatibility alias used by UI/tests for the extracted file kind."""
        return self.kind or self.extension or self.ext

    @property
    def processed_unit_label(self) -> str:
        return self.item_unit or self.unit_label

    @property
    def raw_character_count(self) -> int:
        return self.raw_char_count or self.raw_chars

    @property
    def extracted_character_count(self) -> int:
        return self.extracted_char_count or self.extracted_chars or self.prompt_chars or len(self.text or "")

    @property
    def unit_summary(self) -> str:
        count = self.item_count or self.unit_count or self.processed_units or self.total_units
        unit = self.item_unit or self.unit_label
        return f"{count} {unit}".strip() if count else ""

    @property
    def original_char_count(self) -> int:
        return self.raw_character_count


class ClarifyingQuestion(BaseModel):
    question: str
    why: str = ""
    suggested_answer: str = ""


class ClarificationSet(BaseModel):
    questions: List[ClarifyingQuestion] = Field(default_factory=list)
