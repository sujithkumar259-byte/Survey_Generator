# Prompt files — how to read and edit them

Every instruction Claude (or your chosen model) receives in Phase 2 lives in a
plain-text file in this folder. Nothing is hidden in code. You can open any file
in a text editor, change the wording, save, and the next pipeline run uses your
version. This README explains how they work.

## The pipeline, step by step

Phase 2 runs as a sequence of LLM "agents", each with its own prompt file:

| File | Step | What the model is asked to do |
|------|------|-------------------------------|
| `00_shared_system_context.txt` | (shared) | The PMR persona pasted into every step |
| `01_section_suggester.txt` | 1 | Propose the survey's sections (incl. standard ones) |
| `03_hypothesis_mapper.txt` | 3 | Map each hypothesis → section + construct + question type |
| `04_pressure_tester.txt` | 4 | Adversarially QA and correct the mapping |
| `05_question_drafter.txt` | 5 | Draft each section's questions (incl. screener/flow) |
| `08_survey_flow_checker.txt` | 8 | Flag order/priming/burden problems and missing questions |
| `09_quality_reviewer.txt` | 9 | Flag wording / analysis-readiness problems |
| `10_final_consolidator.txt` | 10 | Merge + triage all issues |
| `11_question_reviser.txt` | 11 | Rewrite only the flagged questions |
| `domain_experts/*.txt` | 7 | Study-type-specific review (see below) |

(Steps 2 and 6 are not LLM steps: step 2 is the human review gate, step 6 is the
deterministic rule-based validator in code.)

## How to read a prompt file

Each file has three kinds of lines:

1. **Comment lines** start with `#`. They are documentation for you and are
   stripped out before anything is sent to the model. The header block at the
   top of each file explains what the step does and which placeholders it has.

2. **Section markers** `### SYSTEM` and `### USER` split the file into the two
   parts every chat model takes:
   - `### SYSTEM` — the model's role and standing instructions.
   - `### USER` — the actual task, plus the data for this run.

3. **Everything else** is the prompt text itself. Edit it freely.

## Placeholders — leave these intact

Text in `{curly_braces}` is a placeholder the app fills in at run time. For
example `{hyp_block}` becomes the JSON list of your hypotheses. Each file's
header lists the placeholders available to it. If you delete or rename a
placeholder, that data will simply not reach the model, so keep them as-is and
edit the words around them.

A literal brace (e.g. in a JSON example the model should copy) is written as a
double brace `{{ }}`. The app turns `{{` into `{` automatically. You normally
don't need to touch these.

## The domain experts (step 7)

Step 7 is "conditional": it runs a reviewer specialised for your **study type**.
The app looks at the study type you enter and picks the matching file from
`domain_experts/`:

- `segmentation.txt` — segmentation / typing-tool studies
- `atu.txt` — awareness, trial & usage / tracking
- `conjoint.txt` — conjoint / DCE / MaxDiff trade-off
- `dtc.txt` — direct-to-consumer / patient
- `generic.txt` — fallback when nothing matches

It matches on common synonyms (e.g. "A&U" → atu, "DCE" → conjoint, "patient" →
dtc). **To add a new study type**, just drop a new file in `domain_experts/`
named after the study type in lower case with underscores for spaces
(e.g. `message_testing.txt`), following the same `### SYSTEM` / `### USER`
structure. The app will pick it up automatically — no code change needed.

## Tips

- Make one change at a time and re-run, so you can see its effect.
- Keep the JSON shape at the end of each `### USER` block intact — the app reads
  the model's reply as JSON, so changing the requested fields can break parsing.
- The wording, checklists, rules, and emphasis are all yours to tune.
