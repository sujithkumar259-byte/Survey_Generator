"""Phase 3 Python renderer (faithful port of the Stage-3 R/officer format).

Guards: (1) the internal [VALIDATION: ...] tag is never printed (the one
intentional change vs the R renderer); (2) the R per-type tables render
(percent table with AUTOSUM total; rating table with anchors).
"""
from __future__ import annotations

import openpyxl
from docx import Document

from survey_generator.contract import OUTLINE_COLS
from survey_generator.phase3 import render_questionnaire_docx


def _workbook(tmp_path, *outline_rows):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Survey_Metadata"
    ws.append(["Field", "Value", "Notes"])
    ws.append(["Study_Title", "ATTR-CM ATU", ""])
    ws.append(["Client_Name", "Acme Pharma", ""])
    o = wb.create_sheet("Survey_Outline")
    o.append(OUTLINE_COLS)
    for outline_row in outline_rows:
        row = {c: "" for c in OUTLINE_COLS}
        row.update(outline_row)
        o.append([row[c] for c in OUTLINE_COLS])
    path = tmp_path / "wb.xlsx"
    wb.save(str(path))
    return str(path)


def _text(docx_path):
    d = Document(docx_path)
    parts = [p.text for p in d.paragraphs]
    parts += [c.text for t in d.tables for r in t.rows for c in r.cells]
    return "\n".join(parts)


def test_validation_tag_is_never_printed(tmp_path):
    wb = _workbook(tmp_path, {
        "Section_ID": "SCR", "Section_Title": "Section 0: Screener",
        "Question_ID": "SCR_Q01", "Question_Text": "How many years in practice?",
        "Question_Type": "NUMERIC",
        "Statements_or_Options": "[1 | Years in practice]",
        "Programming_Instructions": "RANDOMIZE ROWS",
        "Termination_Logic": "TERMINATE IF SCR_Q01 < 2",
        "Validation_Rule": "MIN:0 MAX:60 WHOLE_NUMBER",   # the one thing dropped vs R
        "Source_Hypotheses": "H001; H002",                 # R never renders this
    })
    blob = _text(render_questionnaire_docx(wb, str(tmp_path / "q.docx")))
    assert "MIN:0 MAX:60" not in blob          # [VALIDATION: ...] suppressed
    assert "H001" not in blob
    # kept exactly as the R renderer does
    assert "SCR_Q01" in blob
    assert "[RANDOMIZE ROWS]" in blob
    assert "TERMINATE IF SCR_Q01 < 2" in blob
    assert "[NUMERIC QUESTION]" in blob        # R question-type tag


def test_percent_allocation_table(tmp_path):
    wb = _workbook(tmp_path, {
        "Section_ID": "B", "Section_Title": "Section B: Treatment share",
        "Question_ID": "B_Q10", "Question_Text": "What share of patients on each therapy?",
        "Question_Type": "PERCENT_ALLOCATION",
        "Statements_or_Options": "[1 | Stabilizer]\n[2 | Silencer]\n[3 | Untreated]",
        "Validation_Rule": "1|2|3 SUM=100",
    })
    blob = _text(render_questionnaire_docx(wb, str(tmp_path / "alloc.docx")))
    assert "Stabilizer" in blob and "Silencer" in blob
    assert "XX%" in blob                                   # percent placeholder
    assert "[AUTOSUM, MUST SUM TO 100%]" in blob           # appended TOTAL row
    assert "Total" in blob
    assert "1|2|3 SUM=100" not in blob                     # validation suppressed


def test_termination_consolidation_keeps_distinct_drops_redundant_and_meta(tmp_path):
    wb = _workbook(tmp_path, {
        "Section_ID": "SCR", "Section_Title": "Section 0: Screener",
        "Question_ID": "SCR_Q04", "Question_Text": "How many patients per month?",
        "Question_Type": "SINGLE_SELECT",
        "Statements_or_Options": ("[1 | Fewer than 10 patients per month {TERMINATE}]\n"
                                  "[2 | 10 to 49 patients per month]\n"
                                  "[3 | I do not treat this condition {TERMINATE}]"),
        "Programming_Instructions": ("DO NOT RANDOMIZE\nTERMINATE if option 1 is selected\n"
                                     "Top band split into bands (fixes GEN009)"),
        "Option_Level_Logic": "[1 | Fewer than 10 patients per month {TERMINATE}]",
        "Termination_Logic": ("TERMINATE IF respondent sees fewer than 10 patients per month\n"
                              "Select [1] -> TERMINATE\nTERMINATE IF years in practice < 2"),
    })
    blob = _text(render_questionnaire_docx(wb, str(tmp_path / "term.docx")))
    # both distinct terminating options preserved, one clean line each
    assert "[TERMINATE IF option 1 selected]" in blob
    assert "[TERMINATE IF option 3 selected]" in blob
    # a distinct (non-option) numeric termination is kept
    assert "TERMINATE IF years in practice < 2" in blob
    # genuine instruction kept
    assert "[DO NOT RANDOMIZE]" in blob
    # redundant restatements dropped
    assert "Select [1] -> TERMINATE" not in blob
    assert "TERMINATE if option 1 is selected" not in blob
    # internal change-rationale / IDs stripped
    assert "GEN009" not in blob and "split into" not in blob


def test_many_terminating_options_collapse_to_one_line(tmp_path):
    opts = "\n".join(f"[{i} | Role {i} {{TERMINATE}}]" for i in range(1, 6)) + "\n[6 | Qualifying role]"
    wb = _workbook(tmp_path, {
        "Section_ID": "SCR", "Section_Title": "Section 0: Screener",
        "Question_ID": "SCR_Q01", "Question_Text": "Which best describes your role?",
        "Question_Type": "MULTI_SELECT",
        "Statements_or_Options": opts,
        "Programming_Instructions": ("DO NOT RANDOMIZE\n"
                                     "TERMINATE immediately if respondent selects any of options 1, 2, 3, 4, or 5\n"
                                     "Option 6 must be exclusive"),
        "Termination_Logic": ("TERMINATE if respondent selects any of options 1, 2, 3, 4, or 5\n"
                              "Select any of options 1, 2, 3, 4, or 5 -> TERMINATE"),
    })
    blob = _text(render_questionnaire_docx(wb, str(tmp_path / "many.docx")))
    # one combined terminate line, not five
    assert "[TERMINATE IF options 1, 2, 3, 4 or 5 selected]" in blob
    assert "[TERMINATE IF option 1 selected]" not in blob       # not per-option here
    # plural free-text restatements dropped
    assert "TERMINATE immediately if respondent selects any of options" not in blob
    assert "Select any of options 1, 2, 3, 4, or 5 -> TERMINATE" not in blob
    # genuine directives kept
    assert "[DO NOT RANDOMIZE]" in blob
    assert "Option 6 must be exclusive" in blob


def test_study_rationale_notes_are_stripped(tmp_path):
    note = ("NOTE TO PROGRAMMER: WAINUA (eplontersen) brand name has been removed from this "
            "question; this question uses only a generic descriptor. Brand-specific switching "
            "intent is asked in Section I after brand exposure is established.")
    wb = _workbook(tmp_path, {
        "Section_ID": "F", "Section_Title": "Section F: Switching",
        "Question_ID": "F_Q02", "Question_Text": "How likely are you to switch?",
        "Question_Type": "SINGLE_SELECT",
        "Statements_or_Options": "[1 | Very likely]\n[2 | Not likely]",
        "Programming_Instructions": f"DO NOT RANDOMIZE\n{note}",
        "Notes": note,
    })
    blob = _text(render_questionnaire_docx(wb, str(tmp_path / "rat.docx")))
    assert "[DO NOT RANDOMIZE]" in blob                 # real directive kept
    assert "NOTE TO PROGRAMMER" not in blob             # rationale stripped (programming tag)
    assert "brand name has been removed" not in blob    # ...and from the NOTE: line
    assert "asked in Section" not in blob


def test_collapses_every_phrasing_of_a_single_option_termination(tmp_path):
    # mirrors the screenshot wall: the same 'option 1 terminates' rule restated 5 ways
    wb = _workbook(tmp_path, {
        "Section_ID": "SCR", "Section_Title": "Section 0: Screener",
        "Question_ID": "SCR_Q02", "Question_Text": "Are you a paid consultant?",
        "Question_Type": "SINGLE_SELECT",
        "Statements_or_Options": "[1 | Yes {TERMINATE}]\n[2 | No]",
        "Programming_Instructions": "TERMINATE if code 1 selected",
        "Option_Level_Logic": "TERMINATE if response = 1",
        "Skip_Logic": "code 1 selected",                          # SHOW LOGIC restating the trigger
        "Termination_Logic": "code 1 selected -> TERMINATE",
    })
    blob = _text(render_questionnaire_docx(wb, str(tmp_path / "wall.docx")))
    assert "[TERMINATE IF option 1 selected]" in blob             # the single canonical line
    assert "code 1 selected" not in blob                          # every restatement gone
    assert "response = 1" not in blob
    assert "-> TERMINATE" not in blob
    assert "SHOW LOGIC" not in blob


def test_numeric_threshold_termination_collapses_but_directive_kept(tmp_path):
    # mirrors the numeric screenshot: 3 phrasings of one threshold + a mislabeled SHOW LOGIC
    wb = _workbook(tmp_path, {
        "Section_ID": "SCR", "Section_Title": "Section 0: Screener",
        "Question_ID": "SCR_Q05", "Question_Text": "How many patients do you manage?",
        "Question_Type": "NUMERIC",
        "Statements_or_Options": "[1 | Patients per month]",
        "Programming_Instructions": "FORCE numeric integer entry; range validation 1-2000\nTERMINATE if entry = 0",
        "Skip_Logic": "entry < 1",
        "Termination_Logic": "TERMINATE if response < 1\nentry < 1 -> TERMINATE",
    })
    blob = _text(render_questionnaire_docx(wb, str(tmp_path / "num.docx")))
    assert "FORCE numeric integer entry" in blob                  # real directive kept
    # exactly one threshold-termination phrasing survives (collapsed), not all three
    assert ("entry = 0" in blob) ^ ("response < 1" in blob)
    assert "-> TERMINATE" not in blob
    assert "SHOW LOGIC" not in blob


def test_rating_table_renders_with_anchors(tmp_path):
    wb = _workbook(tmp_path, {
        "Section_ID": "C", "Section_Title": "Section C: Attitudes",
        "Question_ID": "C_Q01", "Question_Text": "Rate your agreement.",
        "Question_Type": "RATING_7_IMPORTANCE",
        "Statements_or_Options": "[1 | Efficacy matters]\n[2 | Safety matters]",
    })
    blob = _text(render_questionnaire_docx(wb, str(tmp_path / "rate.docx")))
    assert "Efficacy matters" in blob
    assert "Not at all important" in blob and "Very important" in blob   # anchors
    assert "[A]" in blob                                                  # column-id tag
