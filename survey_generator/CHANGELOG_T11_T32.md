# T11-T32 implementation notes

This update builds on the T01-T10 hardening pass and implements Phase 1 ingestion/review improvements plus Phase 2 section mapping and retry hardening.

## Phase 1 extraction and source review

- T11: PDF extraction now uses `pypdf` and reports page counts, pages with no extractable text, extraction warnings, and character counts.
- T12: PowerPoint extraction now uses `python-pptx` and extracts slide titles, body text, tables, and best-effort speaker notes from the PPTX package.
- T13: Added per-file extraction summaries with filename, type, processed unit count, extracted characters, prompt characters, truncation, weak-extraction status, and warnings/errors.
- T14: Added weak-extraction detection for empty, sparse, scanned/image-heavy, and chart-heavy files.
- T15: Added an extracted-text preview expander in the Phase 1 UI.
- T16: Added downloadable extraction reports in Markdown and XLSX formats.
- T17: Replaced single hard cutoff behavior with source-aware truncation that preserves beginning, middle, and end excerpts and records a truncation warning.
- T24: Added scanned/image-heavy PDF warnings when PDF pages have little or no text layer.
- T25: Added optional OCR hook controlled by `PHASE1_ENABLE_OCR=1`. OCR remains off by default and reports clear dependency/setup warnings if enabled without OCR dependencies.
- T26: Improved Excel extraction to include sheet names, dimensions, detected header/first row, and sampled data rows rather than a flat cell dump.
- T27: Improved PowerPoint extraction with slide structure, tables, image-only slide warnings, and best-effort speaker notes.

## Phase 1 hypothesis review and export

- T18: Added deterministic hypothesis schema/quality validation before Phase 2 handoff.
- T19: Added duplicate and near-duplicate hypothesis detection.
- T20: Added review statuses: Approved, Needs Edit, and Rejected. Only Approved rows move into Phase 2.
- T21: Added an editable Phase 1 hypothesis table for text, framework/category, rationale, business question, audience, priority, review status, notes, and source traceability.
- T22: Preserved source traceability fields on hypotheses, including source files and source excerpt fields.
- T23: Added Phase 1 hypothesis XLSX export with Hypotheses, Source_Files, Extraction_Warnings, and Hypothesis_Validation sheets.

## Phase 2 mapping and retry hardening

- T28: Phase 2 mapping now uses stable `section_id` values rather than relying on exact section-title matching.
- T29: Updated Phase 2 prompts to require exact section IDs in hypothesis mapping, pressure testing, and section drafting.
- T30: Added deterministic hypothesis-to-section mapping validation before drafting. The pipeline now stops if hypotheses are missing, duplicated, or mapped to nonexistent sections.
- T31: Added LLM JSON retry behavior for invalid JSON, wrong top-level type, empty responses, and malformed model output.
- T32: Added provider-agnostic API retry/backoff for transient provider failures such as rate limits, timeouts, temporary connection failures, and 5xx-style errors.

## Additional guardrails included

- Empty section drafts now retry through the JSON/schema path and block the run if a section still returns zero valid questions.
- Phase 2 no longer silently drops failed section drafts.
- Auto-fix issues from the review loop are routed through the reviser instead of being silently discarded.
- New questions returned by the question reviser are preserved rather than ignored.
