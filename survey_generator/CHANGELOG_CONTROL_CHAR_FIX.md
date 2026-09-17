# Control-character extraction and UI failsafe patch

Implemented after a Phase 1 PPTX upload exposed `openpyxl.utils.exceptions.IllegalCharacterError` caused by hidden control characters such as vertical tab (`\x0b`) in extracted PowerPoint text.

## Changes

- Added app-wide text sanitization helpers in `runtime.py` for hidden XML/XLSX-illegal control characters.
- Sanitized Phase 1 extracted text during cleaning and when loading older `SourceDoc` session state.
- Sanitized XLSX export rows for Phase 1 extraction reports, Phase 1 hypothesis exports, Phase 2 survey-spec exports, and Phase 3 inspection report exports.
- Added Excel cell-length protection to avoid oversized cell issues in downloadable workbooks.
- Made PPTX extraction more fault-tolerant at slide/shape level so one problematic shape does not empty the whole deck.
- Changed the Phase 1 extraction summary so it shows counts and a short issue summary by default, not full warning/error text.
- Added a collapsed warning/error details expander for users who want diagnostics.
- Changed app-wide error display to show a short friendly message by default with technical details collapsed.
- Wrapped Phase 1 XLSX download generation in failsafes so users can continue even if a report export fails.
- Added a regression test for PPTX extraction containing `\x0b`, confirming the extracted text is sanitized and XLSX reports no longer crash.

## Validation

- `python3 -m compileall -q .` passed.
- `PYTHONPATH=.. pytest -q` passed: 26 tests.
- Manual smoke test confirmed a synthetic PPTX with `\x0b` extracts successfully and both extraction/hypothesis XLSX exports are generated.
