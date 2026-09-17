"""Phase 3 — Word questionnaire renderer (Python / python-docx).

Consumes the Survey-Outline workbook contract (the same one Phase 2 writes) and
produces a clean, professional .docx. This is the single-codebase replacement for
the standalone R Shiny renderer.
"""
from .word_renderer import render_questionnaire_docx  # noqa: F401
