"""
Phase 2 -> Phase 3 handoff: write the survey spec to the workbook contract
consumed by Phase 3.
"""
from __future__ import annotations

import re
from datetime import date
from typing import List

import openpyxl
from openpyxl.styles import Alignment, Font, PatternFill

from .metrics import estimate_survey_length_minutes
from .models import Hypothesis, SectionProposal, StudySetup, SurveyQuestion, ValidationReport
from survey_generator.contract import OUTLINE_COLS, WORKBOOK_CONTRACT_VERSION
from survey_generator.runtime import excel_safe_row


TERMINAL_ROUTE_TOKENS = {"END", "TERMINATE", "TERMINATION", "SCREENOUT", "SCREEN_OUT", "SUBMIT"}


def _append(ws, values):
    ws.append(excel_safe_row(list(values)))


def _options_to_cell(options: List[str]) -> str:
    return "\n".join(options or [])


def _programming_cell(instr: List[str]) -> str:
    return "\n".join(instr or [])


def _terminal_logic_from_options(options: list[str]) -> list[str]:
    out: list[str] = []
    for opt in options or []:
        if re.search(r"\{[^{}]*(terminate|screen\s*out|screenout)[^{}]*\}", opt, re.I):
            clean = re.sub(r"^\[|\]$", "", opt).strip()
            out.append(clean)
    return out


def _termination_cell(q: SurveyQuestion) -> str:
    lines = list(q.termination_logic or [])
    lines.extend(_terminal_logic_from_options(q.options or []))
    if q.routing and q.routing.next and q.routing.next.strip().upper() in TERMINAL_ROUTE_TOKENS:
        cond = q.routing.condition or "If routing condition is met"
        lines.append(f"{cond} -> {q.routing.next}")
    return _programming_cell(list(dict.fromkeys([ln for ln in lines if ln])))


def _skip_logic_cell(q: SurveyQuestion) -> str:
    lines = []
    if q.routing and q.routing.condition and q.routing.condition.lower() != "all respondents":
        lines.append(q.routing.condition)
    return _programming_cell(lines)


def _notes_cell(q: SurveyQuestion) -> str:
    notes = []
    if q.flag_status == "flagged" and q.flag_description:
        notes.append("FLAG: " + q.flag_description)
    if q.interviewer_notes:
        notes.append("INTERVIEWER: " + q.interviewer_notes)
    return "\n".join(notes)


def write_survey_spec(
    path: str,
    study: StudySetup,
    questions: List[SurveyQuestion],
    hypotheses: List[Hypothesis],
    sections: List[SectionProposal],
    coverage_map: dict | None = None,
    *,
    validation_status: str = "",
    export_mode: str = "Draft",
    blocker_override: bool = False,
    validation_report: ValidationReport | None = None,
    export_override: bool | None = None,
    final_approved: bool = False,
):
    if validation_report is not None:
        validation_status = validation_report.status
        if not validation_status:
            validation_status = "PASS" if validation_report.passed else "BLOCKER"
    if export_override is not None:
        blocker_override = bool(export_override)
    if final_approved and not blocker_override:
        export_mode = "Final"
    wb = openpyxl.Workbook()

    # ---- Survey_Metadata ----
    ws = wb.active
    ws.title = "Survey_Metadata"
    _append(ws, ["Field", "Value", "Notes"])
    meta_rows = [
        ("Contract_Version", WORKBOOK_CONTRACT_VERSION, "Workbook contract consumed by Phase 3."),
        ("Study_Title", study.study_title, "Used on the cover page."),
        ("Client_Name", study.client_name, "Used on the cover page."),
        ("Draft_Label", "Draft Questionnaire" if export_mode != "Final" else "Final Questionnaire", "Document label."),
        ("Version_Label", "v1.0", "Editable version."),
        ("Last_Updated_Date", date.today().isoformat(), "Explicit deterministic date."),
        ("Survey_Type", "HCP", "HCP or PATIENT."),
        ("Audience_Description", study.target_population, "Used in respondent introduction."),
        ("Estimated_Length_Minutes", str(estimate_survey_length_minutes(questions)), "Deterministic LOI estimate."),
        ("Prepared_By", "ZS Associates", "Cover page footer."),
        ("Validation_Status", validation_status, "PASS, WARNING, or BLOCKER at export time."),
        ("Validation_Blocker_Count", len(validation_report.blockers) if validation_report else "", "Number of blockers at export time."),
        ("Validation_Warning_Count", len(validation_report.warnings) if validation_report else "", "Number of warnings at export time."),
        ("Blocker_Override", "Yes" if blocker_override else "No", "Whether a user exported despite blockers."),
        ("Final_Approved", "Yes" if final_approved else "No", "Whether the human approval gate was checked."),
        ("Export_Mode", export_mode, "Draft or Final."),
        ("Programming_Instructions_Text", "", "Optional multiline override for programming-instruction front matter."),
        ("Sample_Plan_Notes", "", "Optional sample-plan notes shown above/below the sample plan."),
        ("Screening_Criteria_Intro", "", "Optional intro text shown before the screening criteria table."),
        ("Respondent_Intro_Text", "", "Optional multiline respondent introduction override."),
        ("Respondent_Instructions", "", "Optional multiline respondent instructions shown as bullets."),
        ("Confidentiality_Text", "Your responses will be kept confidential and reported only in aggregate.", "Optional confidentiality language."),
    ]
    for r in meta_rows:
        _append(ws, list(r))

    # sample plan block
    _append(ws, [])
    _append(ws, ["Segment_ID", "Audience_Segment", "Target_N", "Qualification_Notes"])
    seen_segments = []
    for h in hypotheses:
        if h.target_segment and h.target_segment not in seen_segments and h.target_segment.lower() != "all":
            seen_segments.append(h.target_segment)
    if not seen_segments:
        seen_segments = ["Primary target"]
    for i, seg in enumerate(seen_segments, start=1):
        _append(ws, [f"SEG{i}", seg, "", f"Qualifies for {seg} readout."])

    # ---- Survey_Outline ----
    ws2 = wb.create_sheet("Survey_Outline")
    _append(ws2, OUTLINE_COLS)
    for idx, q in enumerate(questions, start=1):
        scale_min = q.scale.min if q.scale else ""
        scale_max = q.scale.max if q.scale else ""
        scale_low = q.scale.anchor_low if q.scale else ""
        scale_high = q.scale.anchor_high if q.scale else ""
        scale_validation = ""
        if q.scale:
            scale_validation = "|".join(str(i) for i in range(q.scale.min, q.scale.max + 1))
        routing_next = q.routing.next if (q.routing and q.routing.next) else ""
        row_by_col = {
            "Section_ID": q.section_id or q.section,
            "Section_Title": q.section_title or q.section,
            "Display_Order": f"{idx:03d}",
            "Question_ID": q.q_id,
            "Question_Text": q.question_text,
            "Question_Type": q.question_type,
            "Statements_or_Options": _options_to_cell(q.options),
            "Grid_Columns": _options_to_cell(q.grid_columns),
            "Dropdown_Options": _options_to_cell(q.dropdown_options),
            "Theme": "",
            "Audience_Split": "ALL",
            "Programming_Instructions": _programming_cell(q.programming_instructions),
            "Question_Skip_Logic": _skip_logic_cell(q),
            "Option_Level_Logic": _programming_cell(q.option_level_logic),
            "Termination_Logic": _termination_cell(q),
            "Validation_Rule": scale_validation,
            "Section_Objective": q.section_objective,
            "Notes": _notes_cell(q),
            "Scale_Min": scale_min,
            "Scale_Max": scale_max,
            "Scale_Anchor_Low": scale_low,
            "Scale_Anchor_High": scale_high,
            "Routing_Next": routing_next,
            "Source_Hypotheses": "; ".join(q.hypothesis_ids or []),
            "Interviewer_Notes": q.interviewer_notes or q.notes,
        }
        _append(ws2, [row_by_col.get(c, "") for c in OUTLINE_COLS])

    # ---- Best_Practices (reference) ----
    ws3 = wb.create_sheet("Best_Practices")
    _append(ws3, ["Area", "Recommendation", "Why it matters"])
    _append(ws3, ["Coverage", "Every hypothesis maps to at least one question.", "Traceability is the product's core differentiator."])
    _append(ws3, ["Question types", "Use only the 17 supported renderer types.", "Guarantees the spec renders without placeholders."])
    _append(ws3, ["Routing", "Populate both condition and destination.", "Skip logic needs both trigger and target."])

    # ---- Hypothesis coverage map (reference) ----
    ws4 = wb.create_sheet("Hypothesis_Coverage")
    _append(ws4, ["Hypothesis_ID", "Hypothesis_Text", "Questions_Covering"])
    h_text = {h.hypothesis_id: h.text for h in hypotheses}
    cov = coverage_map or {}
    for h in hypotheses:
        qs = cov.get(h.hypothesis_id, [])
        _append(ws4, [h.hypothesis_id, h_text.get(h.hypothesis_id, ""), ", ".join(qs)])

    # ---- Validation report (reference) ----
    ws5 = wb.create_sheet("Validation_Report")
    _append(ws5, ["Severity", "Issue_Type", "Question_ID", "Message", "Recommended_Fix", "Status"])
    if validation_report:
        for issue in validation_report.issue_rows:
            _append(ws5, [
                issue.get("severity", ""), issue.get("issue_type", ""), issue.get("q_id", ""),
                issue.get("message", ""), issue.get("recommended_fix", ""), issue.get("status", "Open"),
            ])

    # ---- styling: header rows ----
    header_fill = PatternFill("solid", fgColor="071D49")
    header_font = Font(color="F7F9FC", bold=True)
    for sheet in (ws, ws2, ws3, ws4, ws5):
        for cell in sheet[1]:
            cell.fill = header_fill
            cell.font = header_font
            cell.alignment = Alignment(vertical="top", wrap_text=True)
        for row in sheet.iter_rows():
            for cell in row:
                cell.alignment = Alignment(vertical="top", wrap_text=True)

    widths = [10, 24, 12, 14, 50, 22, 40, 24, 24, 16, 12, 28, 22, 22, 24, 20, 30, 24, 10, 10, 20, 20, 16, 22, 24]
    for i, w in enumerate(widths[:len(OUTLINE_COLS)], start=1):
        ws2.column_dimensions[openpyxl.utils.get_column_letter(i)].width = w

    wb.save(path)
    return path
