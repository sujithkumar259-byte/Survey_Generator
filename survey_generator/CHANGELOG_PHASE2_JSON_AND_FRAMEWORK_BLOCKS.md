# Phase 2 JSON-wrapper recovery and Phase 1 framework-block update

## Phase 2 pipeline hardening

- Fixed the `Expected JSON type list; got dict` failure mode by allowing Phase 2 list-producing steps to accept common dict wrappers such as:
  - `{ "sections": [...] }`
  - `{ "mappings": [...] }`
  - `{ "hypothesis_mappings": [...] }`
  - `{ "questions": [...] }`
  - `{ "issues": [...] }`
  - `{ "data": [...] }`
- Added `_coerce_json_list()` in `phase2/pipeline.py` to recover usable arrays from wrapper objects before validation.
- Kept retry behavior for genuinely invalid / empty / malformed model outputs.
- Added regression tests proving section suggestion and the Phase 2 pipeline accept dict-wrapped model responses.

## Phase 1 framework selection redesign

- Replaced the old framework dropdown/multiselect with selectable framework blocks grouped into:
  - HCP Frameworks
  - Patient Frameworks
  - Account Frameworks
  - Consumer Frameworks
- Added the full framework library in `phase1/frameworks.py`.
- Added a Custom framework option where the analyst can enter their own framework prompt.
- Updated Phase 1 prompts so the selected framework blocks guide hypothesis generation.
- Updated the hypothesis CSV contract so `category` means a specific construct/dimension, not the framework name.
- Updated the data editor so `category` is editable text instead of a framework dropdown.

## Hypothesis wording contract

- Updated the Phase 1 generator prompt to enforce the preferred pattern:
  - `[audience] who [specific attribute / belief / barrier / behavior / situation] are more likely to [outcome]`
  - `[audience] who [specific attribute / belief / barrier / behavior / situation] are less likely to [outcome]`
  - `[audience] who [specific attribute / belief / barrier / behavior / situation] are more/less likely to be receptive to [message/product/support/intervention]`
- Updated examples and validation warnings around the preferred wording pattern.

## Validation

Validated with:

```text
python3 -m compileall -q .
PYTHONPATH=/mnt/data/sg_work python3 healthcheck.py
PYTHONPATH=/mnt/data/sg_work pytest -q
```

Result:

```text
42 passed
healthcheck: ok
```
