# T61-T88 implementation notes

This release completes the final Phase 3, testing, deployment, authentication, and persistence tasks on top of the T33-T60 build.

## Phase 3

- T61: Made DOCX exports session-safe by including project/version identifiers in generated questionnaire filenames.
- T62: Added parsed workbook preview in the Phase 3 UI, including metadata, section/question counts, question type counts, warnings, blockers, and parsed question inventory.
- T63: Added Phase 3 inspection report downloads in CSV and XLSX formats via `phase3/reports.py`.
- T64: Added test coverage that verifies the sample workbook covers and renders every supported question type.
- T65: Added a golden DOCX regression test that renders the sample workbook and verifies expected document structure.
- T66: Added optional branded DOCX template support. Uploaded `.docx` templates preserve existing content/styles and generated questionnaire content is appended.
- T67: Added configurable front matter support for intro language, programming notes, confidentiality text, screening criteria text, and sample-plan notes.
- T68: Improved wide-table handling with smaller font sizing, repeated header rows, wrapping, and safer table widths for large grids/matrices.
- T69: Improved long option-label rendering by adding table-cell wrapping helpers and cleaner line breaking.

## Testing

- T70: Added unit tests for PDF, DOCX, PPTX, XLSX, TXT, CSV, unsupported, empty, and weak extraction cases.
- T71: Added unit tests for LLM JSON extraction variants and malformed model responses.
- T72: Added Phase 1 hypothesis validation tests, including duplicate handling and approval filtering.
- T73: Added Phase 2 validator tests for uncovered hypotheses, duplicate IDs, invalid routing, missing options, and missing scale anchors.
- T74: Added Excel writer tests to verify metadata, question fields, scale anchors, routing, termination logic, and source hypotheses are preserved.
- T75: Added Phase 3 workbook parser tests for valid workbooks, missing required columns, invalid status handling, and report export.
- T76: Added integration tests for Phase 2 workbook output through Phase 3 inspection/rendering.
- T77: Added mocked full-pipeline tests so Phase 2 orchestration can be tested without live LLM calls.

## Deployment

- T78: Added `.env.example` with LLM keys, runtime configuration, upload limits, retention, OCR, auth, and cost-estimation settings.
- T79: Added Dockerfile for reproducible Streamlit deployment.
- T80: Added docker-compose deployment configuration.
- T81: Added `healthcheck.py` for lightweight deployment health checks.
- T82: Added GitHub Actions CI workflow for syntax checks and unit/integration tests.
- T83: Pinned runtime and dev dependencies in `requirements.txt` and `requirements-dev.txt`.

## Authentication and persistence

- T84: Added optional authentication support through `auth.py`, with disabled-by-default local mode, password/token auth, and proxy/SSO mode.
- T85: Added durable file-backed project persistence through `persistence.py`, including project records, snapshots, artifacts, and audit logs.
- T86: Added project status workflow states and Streamlit UI visibility for current status.
- T87: Added artifact versioning for exported workbooks, DOCX questionnaires, templates, snapshots, and validation artifacts.
- T88: Added audit-trail capture and download support for major user/project actions.

## Validation performed

- `python3 -m compileall -q .`
- `PYTHONPATH=/mnt/data/work_t61_t88 pytest -q` -> 25 passed
- `PYTHONPATH=/mnt/data/work_t61_t88 python3 healthcheck.py` -> ok
- Phase 3 sample workbook inspection/render smoke test passed
- ZIP integrity test passed after packaging
