# Phase 1 CSV Hypothesis Contract Patch

## Why hypothesis generation was failing

Phase 1 previously asked the model for JSON and then tried to coerce many possible JSON shapes into `Hypothesis` objects. In live runs, the model could return prose, markdown, a framework-keyed object, a JSON object with unexpected keys, or a partially valid structure. When none of those shapes produced rows with recognizable hypothesis text, the pipeline raised: `The model did not return any usable hypotheses`.

The failure was not necessarily that the model had no ideas. It was usually an output-contract mismatch: the prompt wanted one schema, but the model returned another schema.

## What changed

- Phase 1 generator prompt now requires one simple CSV text contract:

```csv
hypotheses number,category,Hypotheses,rationale
H001,"Drivers & barriers","Adoption is higher among HCPs who perceive long-term outcomes evidence as credible and differentiated.","Tests a measurable belief that can distinguish evidence-led segments and guide message strategy."
```

- The generator now calls `complete_text()` instead of `complete_json()` for Phase 1 hypotheses.
- Added `phase1/csv_contract.py` with a defensive parser for the four-column CSV output.
- Parser handles plain CSV, fenced CSV, TSV, semicolon-delimited output, markdown tables, accidental preamble text, and common unquoted-comma mistakes.
- If parsing fails, Phase 1 retries with a stricter CSV repair instruction.
- If retries still fail, the app creates editable starter rows so the user can continue.
- The Phase 1 UI now includes a collapsed output-contract/diagnostics expander.
- The Phase 1 download area now includes a simple CSV export with exactly the four requested columns.
- Phase 2 hypothesis upload parsing now recognizes `hypotheses number` and `Hypotheses` headers.

## Required output columns

```text
hypotheses number,category,Hypotheses,rationale
```

## Validation

- `python3 -m compileall -q .` passed.
- `python3 healthcheck.py` returned `ok`.
- `pytest -q` passed: 36 tests.
