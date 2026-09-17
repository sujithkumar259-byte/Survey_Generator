# Hypothesis-Driven Survey Generator

A unified Streamlit dashboard that turns source material and approved research hypotheses into a validated **survey-outline workbook**, in two AI phases. The final Word document (Phase 3) is rendered by the separate **Stage-3 R Shiny app**, which consumes the same workbook contract.

| Phase | What it does | AI? |
|-------|--------------|-----|
| **1 — Hypothesis Generation** | Upload source files + study context → framework-based hypotheses | yes |
| **2 — Survey Outline** | Approved hypotheses + study setup → clean, validated survey-outline workbook (`.xlsx`) | yes |
| **3 — Questionnaire Document** | Survey-outline workbook → formatted `.docx` | **external — Stage-3 R Shiny app** |

One top panel configures the LLM provider, key and model used by Phases 1 & 2.

## Setup

```bash
pip install -r requirements.txt
streamlit run app.py
```

Open the local URL Streamlit prints, usually http://localhost:8501.

## Supported uploads

Phase 1 source material supports:

```text
.pdf, .docx, .pptx, .xlsx, .csv, .txt, .md
```

Legacy binary Office formats are intentionally not advertised or parsed:

```text
.doc, .ppt, .xls
```

Convert those files to `.docx`, `.pptx`, or `.xlsx` before uploading.

Default upload limits are configurable through environment variables:

```text
MAX_UPLOAD_MB=25
MAX_TOTAL_UPLOAD_MB=100
FILE_RETENTION_HOURS=24
SURVEY_GENERATOR_TEMP_ROOT=/tmp/survey_generator
```

## Using it

1. **API Configuration**: pick a provider, paste your API key, pick a model, and hit **Test connection**. API keys are stored only in the active Streamlit server session and are not written to disk or logged by this app.
2. **Phase 1 tab**: upload source material, complete the study brief, choose frameworks, ask clarifying questions, generate hypotheses, then review and approve the hypothesis set for Phase 2.
3. **Phase 2 tab**: edit the study setup and approved hypotheses, propose sections, review/edit/lock the section plan at the human gate, run the AI pipeline, then download the survey-outline workbook (`.xlsx`).
4. **Phase 3 (Stage-3 R Shiny app)**: open the downloaded survey-outline workbook in the separate Stage-3 R Shiny app to render and download the formatted Word document.

## Privacy and operational behavior

- Uploaded files, extracted text, hypotheses, and generated survey content are sent to the selected LLM provider during Phases 1 and 2.
- API keys are stored only in the active Streamlit server session and are never written to disk by this app.
- Each Streamlit session/project gets an isolated temp folder under `SURVEY_GENERATOR_TEMP_ROOT`.
- Old project folders are cleaned up using `FILE_RETENTION_HOURS`.
- Structured logs are written as JSONL in the project folder, but the app avoids logging raw source text, prompts, generated survey text, or API keys.

## Phase 2 pipeline

1. Section Suggester → human gate for section review and lock
3. Hypothesis Mapper
4. Pressure Tester
5. Question Drafter
6. Deterministic Validator
7. Conditional Domain Expert
8. Survey Flow Checker
9. Quality Reviewer
10. Final Consolidator
11. Question Reviser

Steps 7–11 form a review/revision loop that runs at most 3 rounds, exiting early when no major issues remain. Escalated questions are flagged for human review.

## The Phase 2 → Stage-3 contract

Phase 2 emits the workbook the Stage-3 R Shiny app consumes (defined in `contract.py`):

- **Survey_Metadata** — key/value study fields, then a `Segment_ID`-keyed sample plan.
- **Survey_Outline** — fixed-order questionnaire rows.
- 17 supported question types only.
- Item syntax `[code | label]`, inline logic in `{curly braces}`, dual pairs split on `vs`.

The Stage-3 R Shiny app is deterministic and uses this workbook contract to generate the formatted Word questionnaire.

## Project layout

```text
survey_generator/
├── app.py                  # Streamlit dashboard (Phases 1 & 2)
├── contract.py             # shared survey-outline workbook contract (Phase 2 → Stage-3 R app)
├── runtime.py              # project/session folders, upload limits, cleanup, logging
├── requirements.txt
├── llm/
│   ├── client.py           # multi-provider LLM client
│   └── prompts.py          # prompt builders
├── phase1/
│   ├── extract.py          # file extraction
│   ├── models.py
│   └── pipeline.py
└── phase2/
    ├── models.py
    ├── pipeline.py
    ├── validator.py
    ├── excel_writer.py
    └── sample_data.py

# Phase 3 (Word rendering) lives in the separate Stage-3 R Shiny app,
# which reads the same survey-outline workbook contract (contract.py).
```
