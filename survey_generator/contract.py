"""Shared survey-outline workbook contract.

This is the agreement between Phase 2 (which WRITES the survey-outline workbook)
and the downstream Stage-3 renderer (the standalone R Shiny app) which READS it
to produce the formatted Word questionnaire.

It was previously `phase3/config.py`. The Python Phase-3 renderer has been
retired in favour of the R Shiny Stage-3 app, but the contract constants are
still needed by Phase 2 (editor, validator, Excel writer) and the prompts, so
they live here in a renderer-agnostic module.
"""

# ---- Workbook contract -------------------------------------------------------
WORKBOOK_CONTRACT_VERSION = "1.1"
SUPPORTED_CONTRACT_MAJOR = "1"
REQUIRED_SHEETS = ["Survey_Metadata", "Survey_Outline"]

# ---- Supported question types (the renderer contract) -----------------------
SUPPORTED_QUESTION_TYPES = [
    "RATING_7", "RATING_7_IMPORTANCE", "RATING_7_SATISFACTION",
    "RATING_7_LIKELIHOOD", "RATING_7_FAMILIARITY", "RATING_DUAL",
    "SINGLE_SELECT", "MULTI_SELECT", "NUMERIC", "PERCENT_ALLOCATION",
    "RANKING", "AWARENESS_USAGE", "DROPDOWN", "OPEN_END", "GRID",
    "TPP_DISPLAY", "INSTRUCTION",
]

# ---- Survey_Outline column contract -----------------------------------------
# Required columns are the original Stage 3 contract. Production-readiness
# fields are optional for backwards compatibility with older/sample workbooks.
REQUIRED_OUTLINE_COLS = [
    "Section_ID", "Section_Title", "Display_Order", "Question_ID",
    "Question_Text", "Question_Type", "Statements_or_Options", "Grid_Columns",
    "Dropdown_Options", "Theme", "Audience_Split", "Programming_Instructions",
    "Question_Skip_Logic", "Option_Level_Logic", "Termination_Logic",
    "Validation_Rule", "Section_Objective", "Notes",
]

OPTIONAL_OUTLINE_COLS = [
    "Scale_Min", "Scale_Max", "Scale_Anchor_Low", "Scale_Anchor_High",
    "Routing_Next", "Source_Hypotheses", "Interviewer_Notes",
]

OUTLINE_COLS = REQUIRED_OUTLINE_COLS + OPTIONAL_OUTLINE_COLS
