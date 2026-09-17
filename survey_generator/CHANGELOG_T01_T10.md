# T01-T10 production-readiness updates

Implemented app-wide P0 updates:

- T01: Added missing declared dependencies for PDF and PowerPoint extraction: `pypdf` and `python-pptx`.
- T02: Removed advertised support for legacy binary Office uploads (`.doc`, `.ppt`, `.xls`) and made the extractor return a clear unsupported-file note if those are passed directly.
- T03/T05: Added per-session `project_id` generation and isolated project folders under `SURVEY_GENERATOR_TEMP_ROOT`.
- T04: Added best-effort cleanup of old project folders using `FILE_RETENTION_HOURS`.
- T06: Added upload extension and size validation using `MAX_UPLOAD_MB` and `MAX_TOTAL_UPLOAD_MB`.
- T07: Added a UI privacy notice clarifying LLM provider data flow and Streamlit server-session API key storage.
- T08: Added prompt-injection protection to the shared system prompt.
- T09: Added structured JSONL logging in each project folder for key operational events without logging raw source text, prompts, generated survey text, or API keys.
- T10: Replaced traceback display in the UI with friendly error messages and logged concise error details.

Validation performed:

- `python -m compileall -q .` passed.
- Runtime/extractor imports passed for `runtime.py` and `phase1/extract.py`.
- Legacy `.doc` files now return an unsupported-file message instead of being treated as `.docx`.
- Phase 3 sample workbook inspection returned READY with 17 questions.
- Phase 3 sample workbook rendered successfully to a test DOCX.

Clean-install note:

- A full fresh `pip install -r requirements.txt` was attempted in a virtual environment, but the execution environment could not reach PyPI due DNS/network resolution failure. The dependency file itself has been updated and syntax/import checks were run where local packages were available.
