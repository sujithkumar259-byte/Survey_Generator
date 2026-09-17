# Phase 1 hypothesis-generation and Streamlit widget failsafe patch

## Fixed

- Added explicit `key=` values to all Streamlit widgets in `app.py`, including duplicated labels across tabs such as `Client`, `Study type`, and `Disease area`. This prevents `StreamlitDuplicateElementId` from crashing the app when switching between Phase 1, Phase 2, and Phase 3.
- Hardened Phase 1 hypothesis parsing so the generator accepts more common LLM JSON shapes, including:
  - a plain JSON array
  - `{ "hypotheses": [...] }`
  - `{ "research_hypotheses": [...] }`
  - framework-keyed dictionaries such as `{ "Drivers & barriers": [...] }`
  - list-of-string hypothesis outputs
  - alternate text fields such as `hypothesis`, `hypothesis_text`, `statement`, and `claim`
- Added a Phase 1 fallback path when the model response cannot be converted into usable hypotheses. The app now creates editable starter rows marked `Needs Edit` instead of leaving the user stuck.
- Added a manual starter-table option when the user needs to proceed without AI-generated hypotheses.
- Kept technical error details hidden inside collapsed expanders, while showing only short user-facing summaries by default.

## Validation

- Added regression tests for:
  - framework-keyed Phase 1 model output
  - clean handling of unrecognized model-output shape
  - fallback starter rows being marked `Needs Edit`
  - all Streamlit widgets having explicit keys
- `python3 -m compileall -q .` passed.
- `pytest -q` passed: 30 tests.
