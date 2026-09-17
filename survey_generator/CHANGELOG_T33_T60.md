# CHANGELOG — T33–T60 production-readiness updates

Implemented Phase 2 pipeline correctness, Phase 2 review/export controls, and Phase 3 workbook-contract safeguards.

## Phase 2 pipeline and validation

- T33: Added hard blocking for empty section drafts. Approved sections that return zero valid questions now trigger an `LLMError` instead of being silently omitted.
- T34: Fixed revision merging so the question reviser can modify existing questions, delete questions, and insert brand-new questions returned in `inserted_questions` or `revised_questions`.
- T35: Routed `auto_fix_issues` into the revision pass so they are no longer discarded.
- T38: Added deterministic post-draft and post-revision question renumbering by locked section order, with routing target remapping.
- T39: Expanded deterministic validation for duplicate/missing question IDs, invalid display order, invalid section assignments, and routing targets that do not point to a valid question or terminal target.
- T49: Added stronger question-type-specific validation for options, grids, dropdowns, ratings, rankings, percent allocations, awareness/usage, and open ends.
- T50: Added deterministic survey-length / LOI estimation.
- T51: Added respondent-burden warnings for overlong surveys, too many grids, open ends, rankings, long option lists, and large batteries.
- T52: Added LLM usage snapshots with call counts, character counts, estimated tokens, and optional cost estimate based on generic environment-configured rates.
- T53: Persisted intermediate Phase 2 artifacts including approved sections, pipeline output, validation reports, revision diffs, renumbering maps, and final edited questions.
- T54: Added revision diff rows to show inserted, deleted, and modified questions after AI review/revision.
- T55: Split large review passes into section/chunk batches to reduce token-limit risk.

## Phase 2 UI and export gate

- T36: Added export policy where blockers disable workbook export unless explicitly overridden.
- T37: Added explicit "Export draft despite blockers" override and stored override status in workbook metadata.
- T40: Preserved scale min/max and scale low/high anchors in the workbook contract.
- T41: Preserved routing destination through a new `Routing_Next` workbook column.
- T42: Preserved termination logic in `Termination_Logic` so Phase 3 can build real screening criteria.
- T43: Preserved source hypothesis coverage through a new `Source_Hypotheses` column.
- T44: Added a final Phase 2 question editor for text, type, options, scales, routing, termination logic, notes, and source hypotheses.
- T45: Re-runs Phase 2 validation after manual edits and exports only the edited/validated version.
- T46: Added a coverage matrix UI showing which questions cover each hypothesis.
- T47: Added an issue dashboard combining deterministic validation issues and AI review/consolidation issues.
- T48: Added a human approval gate before workbook export.

## Phase 3 workbook contract and rendering safeguards

- T56: Added workbook contract versioning via `Contract_Version` metadata and compatibility checks.
- T57: Tightened required-sheet and required-column validation for `Survey_Metadata` and `Survey_Outline`.
- T58: Added duplicate/blank question validation and duplicate display-order checks.
- T59: Added question-type-specific workbook validation independent of Phase 2.
- T60: Added draft vs final render mode. Draft mode allows placeholders/warnings; final mode blocks unresolved placeholders, structural errors, incompatible contract versions, and invalid question rows.

## Validation performed

- `python3 -m compileall -q .` passed.
- Phase 2 mocked pipeline smoke test passed, including successful pipeline output and empty-section-draft blocking.
- Missing-question insertion merge smoke test passed.
- Phase 2 workbook export smoke test passed with scale anchors, routing destination, termination logic, and source hypotheses preserved.
- Phase 3 sample workbook inspection/render smoke test passed in final mode.
- Phase 3 invalid workbook smoke test confirmed final rendering is blocked while draft rendering remains available.
