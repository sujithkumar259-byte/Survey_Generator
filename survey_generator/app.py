"""
Hypothesis-Driven Survey Generator — unified Streamlit dashboard.

Two AI phases in one app (Phase 3 rendering is handled by the separate Stage-3 R Shiny app):
  Phase 1  (AI)       — input docs -> hypotheses
  Phase 2  (AI)       — hypotheses + setup -> clean survey-outline workbook (.xlsx)
  Phase 3  (external) — open the survey-outline workbook in the Stage-3 R Shiny app to render the Word .docx

A top panel configures the LLM provider/key/model used by Phases 1 & 2.
API keys live only in the active Streamlit server session and are never written to disk by this app.
"""
import csv
import io
import json
import os
import sys
from time import perf_counter

import streamlit as st
import streamlit.components.v1 as components

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + "/..")

# Trust the OS / corporate cert store (e.g. Zscaler root CA) for outbound TLS, so
# provider SDKs work behind SSL-inspecting corporate proxies. No-op if unavailable.
try:
    import truststore
    truststore.inject_into_ssl()
except Exception:
    pass

from survey_generator.llm.client import LLMClient, PROVIDERS, DEFAULT_MODELS
from survey_generator.phase2.models import (
    ConsolidatedResult, HMapping, Hypothesis, SectionProposal, StudySetup,
    SurveyQuestion, ValidationReport,
)
from survey_generator.phase2.sample_data import SAMPLE_STUDY, SAMPLE_HYPOTHESES
from survey_generator.phase2 import pipeline
from survey_generator.phase2.review import (
    coverage_matrix_rows, editor_rows_to_questions, issue_dashboard_rows,
    questions_to_editor_rows, validation_policy,
)
from survey_generator.phase2.validator import validate as validate_phase2
from survey_generator.phase2.excel_writer import write_survey_spec
from survey_generator.phase3 import render_questionnaire_docx
from survey_generator.phase1.models import StudyBrief, SourceDoc, FRAMEWORKS
from survey_generator.phase1.frameworks import (
    CUSTOM_FRAMEWORK_NAME, FRAMEWORK_LIBRARY, category_suggestions, selected_framework_prompt_block,
)
from survey_generator.phase1 import extract as p1_extract
from survey_generator.phase1 import pipeline as p1_pipeline
from survey_generator.phase1.validation import (
    APPROVAL_STATUSES, PRIORITIES, approved_hypotheses, has_blockers,
    prepare_hypothesis_rows,
)
from survey_generator.phase1.export import (
    build_extraction_report_text, build_extraction_report_xlsx,
    build_hypothesis_export_xlsx,
)
from survey_generator.phase1.csv_contract import canonical_header_line, csv_example_rows, rows_to_simple_hypothesis_csv
from survey_generator.contract import SUPPORTED_QUESTION_TYPES
from survey_generator.auth import require_authenticated_user
from survey_generator.persistence import PROJECT_STATUSES, get_project_store
from survey_generator.runtime import (
    DEFAULT_RETENTION_HOURS, MAX_TOTAL_UPLOAD_MB, MAX_UPLOAD_MB,
    SUPPORTED_HYPOTHESIS_EXTENSIONS, SUPPORTED_SOURCE_EXTENSIONS,
    SUPPORTED_WORKBOOK_EXTENSIONS, cleanup_old_project_dirs,
    ensure_project_workspace, friendly_error_message, log_event, safe_filename,
    shorten_text, technical_error_details, unique_path, validate_uploaded_files,
    write_bytes_unique,
)


# ============================================================ config / style
st.set_page_config(page_title="Survey Generator", page_icon="◆",
                   layout="wide", initial_sidebar_state="collapsed")

CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=Lora:ital,wght@0,500;0,600;1,500&family=JetBrains+Mono:wght@400;500&display=swap');

:root {
  --bg:#FAF7F2;          /* warm off-white page */
  --card:#FFFCF8;        /* card surface */
  --line:#E8E0D5;        /* borders */
  --text:#2C2825;        /* darkest text (never pure black) */
  --muted:#7A6F65;       /* secondary text */
  --accent:#C17F34;      /* single amber accent */
  --accent-weak:#F5ECD9; /* hover fills + tag backgrounds */
  --sidebar:#F2EDE5;     /* sidebar surface */
  --serif:'Lora', Georgia, 'Times New Roman', serif;
  --sans:'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
  --mono:'JetBrains Mono', 'SFMono-Regular', Menlo, Consolas, monospace;
}

/* ---- page surface + base type ------------------------------------------- */
.stApp { background: var(--bg); color: var(--text); }
html, body, .stApp, .stMarkdown, .stApp p, .stApp li, .stApp span, .stApp label,
[data-testid="stWidgetLabel"] p, [data-testid="stWidgetLabel"] label {
  font-family: var(--sans);
  color: var(--text);
}
.stApp p, .stApp li { font-size:15px; line-height:1.75; }
[data-testid="stCaptionContainer"], .stCaption, small {
  color: var(--muted) !important; font-size:13px; line-height:1.6;
}

/* centred 820px column with generous breathing room */
.block-container { max-width:820px; padding-top:2.6rem; padding-bottom:4rem; }
.main .block-container > div { gap:1.4rem; }

/* ---- headings: Lora serif ----------------------------------------------- */
h1, h2, h3, h4 { font-family: var(--serif) !important; color: var(--text) !important;
  font-weight:600; letter-spacing:0; line-height:1.3; }
h1, .stApp h1 { font-size:28px !important; margin-bottom:.2rem; }
h2, .stApp h2 { font-size:22px !important; }
h3, .stApp h3 { font-size:17px !important; font-weight:600; }

/* numbers / code / data in mono */
code, kbd, pre, .stCode, [data-testid="stMetricValue"], .mono {
  font-family: var(--mono) !important; font-size:13px !important;
}
[data-testid="stMetricValue"] { color: var(--text) !important; font-weight:500; }
[data-testid="stMetricLabel"] { color: var(--muted) !important; font-family:var(--sans) !important; }

/* ---- cards (flat, bordered, no shadow) ---------------------------------- */
.card {
  background: var(--card); border:1px solid var(--line); border-radius:14px;
  padding:1.5rem; margin-bottom:1.4rem; box-shadow:none;
}
/* expander + status as cards */
[data-testid="stExpander"], div[data-testid="stStatusWidget"] {
  background: var(--card); border:1px solid var(--line) !important;
  border-radius:14px; box-shadow:none;
}
[data-testid="stExpander"] summary,
[data-testid="stExpander"] details > summary {
  font-family:var(--sans); color:var(--text); gap:10px;
}
/* keep the expander chevron from crowding the label if the icon font is slow */
[data-testid="stExpander"] summary svg,
[data-testid="stExpander"] summary [data-testid="stIconMaterial"] { flex:0 0 auto; margin-right:4px; }
/* safeguard: if the Material icon webfont is blocked, its ligature text
   ("keyboard_arrow_down") can leak; the proper icon font renders normally. */
[data-testid="stIconMaterial"] {
  font-family:'Material Symbols Rounded','Material Symbols Outlined','Material Icons' !important;
}

/* alerts: soft parchment, amber edge (cover current + legacy testids) */
[data-testid="stAlert"], [data-testid="stAlertContainer"], div[role="alert"] {
  background: var(--accent-weak) !important; border:1px solid var(--line) !important;
  border-radius:12px; color:var(--text) !important;
}
[data-testid="stAlert"] *, div[role="alert"] * { color:var(--text) !important; }

/* ---- accents: kicker label + chips -------------------------------------- */
.kicker {
  display:inline-block; font-family:var(--mono); font-size:11px; letter-spacing:.06em;
  text-transform:none; color:var(--accent); background:var(--accent-weak);
  border:1px solid var(--line); padding:3px 10px; border-radius:8px; margin-bottom:.7rem;
}
.chip {
  display:inline-flex; align-items:center; gap:6px; border-radius:999px;
  padding:4px 12px; font-size:12px; font-weight:500; font-family:var(--sans);
  border:1px solid var(--line); background:var(--card); color:var(--muted) !important;
}
.chip-ok   { color:var(--accent) !important; background:var(--accent-weak); border-color:var(--line); }
.chip-warn { color:var(--accent) !important; background:var(--accent-weak); border-color:var(--accent); }
.chip-off  { color:var(--muted) !important; }

/* brand header */
.brand { display:flex; align-items:center; gap:14px; margin-bottom:.4rem; }
.brand-text { display:flex; flex-direction:column; gap:2px; min-width:0; }
.brand-mark {
  width:42px; height:42px; min-width:42px; border-radius:11px; display:grid; place-items:center;
  font-size:18px; color:var(--accent) !important; background:var(--accent-weak);
  border:1px solid var(--line);
}
.brand-title { font-family:var(--serif); font-size:24px; font-weight:600; color:var(--text); line-height:1.25; }
.brand-sub { font-family:var(--sans); color:var(--muted); font-size:14px; line-height:1.5; }
.status-row { display:flex; gap:8px; margin:0 0 1rem 56px; flex-wrap:wrap; }

/* ---- buttons ------------------------------------------------------------ */
/* secondary = amber outline */
.stButton > button {
  font-family:var(--sans); font-weight:500; font-size:14px; border-radius:10px;
  padding:.5rem 1.1rem; background:var(--card); color:var(--accent);
  border:1px solid var(--accent); box-shadow:none; transition:background .15s ease;
}
.stButton > button:hover { background:var(--accent-weak); color:var(--accent); border-color:var(--accent); }
/* primary = amber filled */
.stButton > button[kind="primary"] {
  background:var(--accent); color:#FFFCF8; border:1px solid var(--accent);
}
.stButton > button[kind="primary"]:hover { background:#A96B27; border-color:#A96B27; color:#FFFCF8; }
/* download buttons inherit secondary look */
.stDownloadButton > button {
  font-family:var(--sans); font-weight:500; border-radius:10px;
  background:var(--card); color:var(--accent); border:1px solid var(--accent); box-shadow:none;
}
.stDownloadButton > button:hover { background:var(--accent-weak); }

/* ---- inputs ------------------------------------------------------------- */
.stTextInput input, .stTextArea textarea, .stNumberInput input,
div[data-baseweb="select"] > div {
  background:#FFFDFA !important; color:var(--text) !important;
  border:1px solid var(--line) !important; border-radius:10px !important;
  font-family:var(--sans) !important;
}
.stTextInput input:focus, .stTextArea textarea:focus,
div[data-baseweb="select"] > div:focus-within {
  border-color:var(--accent) !important; box-shadow:0 0 0 1px var(--accent) !important;
}
.stTextArea textarea { font-family:var(--mono) !important; font-size:13px !important; line-height:1.7; }

/* file uploader as a warm dashed well */
[data-testid="stFileUploaderDropzone"] {
  background:#FFFDFA; border:1px dashed var(--line); border-radius:12px;
}

/* ---- tabs --------------------------------------------------------------- */
.stTabs [data-baseweb="tab-list"] { gap:.4rem; border-bottom:1px solid var(--line); }
.stTabs [data-baseweb="tab"] {
  font-family:var(--sans); font-size:14px; color:var(--muted);
  background:transparent; border:none; padding:.5rem .9rem;
}
.stTabs [aria-selected="true"] { color:var(--accent) !important; }
.stTabs [data-baseweb="tab-highlight"] { background:var(--accent) !important; }

/* ---- sidebar ------------------------------------------------------------ */
[data-testid="stSidebar"] { background:var(--sidebar); border-right:1px solid var(--line); }
[data-testid="stSidebar"] * { font-family:var(--sans); color:var(--text); }

/* ---- dataframe / tables ------------------------------------------------- */
[data-testid="stDataFrame"] { border:1px solid var(--line); border-radius:12px; }
[data-testid="stDataFrame"] * { font-family:var(--mono); font-size:13px; }

/* dividers + misc */
hr { border-color:var(--line); }
.muted { color:var(--muted) !important; }
</style>
"""
st.markdown(CSS, unsafe_allow_html=True)

# ---- demo polish: depth, motion & refinement (purely cosmetic, append-only) ----
# Layers elevation + micro-interactions on top of the flat base theme. Restyles
# existing elements only — no markup, no logic, no behaviour change.
ENHANCE_CSS = """
<style>
/* warm gradient page surface for subtle depth */
.stApp {
  background:
    radial-gradient(1100px 560px at 12% -12%, #FFFFFF 0%, rgba(255,255,255,0) 62%),
    linear-gradient(180deg, #FBF8F3 0%, #F5EFE5 100%) !important;
  background-attachment: fixed !important;
}

/* gentle entrance for the main content column */
.block-container { animation: appRise .55s cubic-bezier(.2,.7,.3,1) both; }
@keyframes appRise { from { opacity:0; transform:translateY(10px); } to { opacity:1; transform:none; } }

/* cards / expanders / status: soft elevation + hover lift */
.card, [data-testid="stExpander"], div[data-testid="stStatusWidget"] {
  box-shadow: 0 1px 3px rgba(44,40,37,.06), 0 12px 28px rgba(44,40,37,.09) !important;
  transition: box-shadow .22s ease, transform .22s ease, border-color .22s ease;
}
.card:hover, [data-testid="stExpander"]:hover {
  box-shadow: 0 3px 8px rgba(44,40,37,.07), 0 16px 34px rgba(44,40,37,.10) !important;
  transform: translateY(-2px);
  border-color:#DBCDB8 !important;
}

/* alerts: keep the parchment look, add a soft warm lift */
[data-testid="stAlert"], [data-testid="stAlertContainer"], div[role="alert"] {
  box-shadow: 0 6px 18px rgba(193,127,52,.10) !important;
}

/* buttons: depth + hover lift; primary gets a warm gradient */
.stButton > button, .stDownloadButton > button {
  box-shadow: 0 1px 2px rgba(44,40,37,.06);
  transition: transform .15s ease, box-shadow .15s ease, background .15s ease;
}
.stButton > button:hover, .stDownloadButton > button:hover {
  transform: translateY(-1px);
  box-shadow: 0 7px 18px rgba(193,127,52,.18);
}
.stButton > button[kind="primary"] {
  background: linear-gradient(135deg, #C9883A 0%, #B06E27 100%) !important;
  box-shadow: 0 3px 10px rgba(176,110,39,.28) !important;
}
.stButton > button[kind="primary"]:hover {
  transform: translateY(-1px);
  box-shadow: 0 9px 22px rgba(176,110,39,.34) !important;
}
.stButton > button:active, .stDownloadButton > button:active { transform: translateY(0); }

/* hero header banner — restyles the existing .brand row (no new markup) */
.brand {
  background: linear-gradient(135deg, #FFFFFF 0%, #F7EEDD 55%, #F0E1C2 100%);
  border:1px solid #E8DCC4; border-radius:16px;
  padding:1.15rem 1.35rem; margin-bottom:1rem;
  box-shadow: 0 2px 6px rgba(44,40,37,.05), 0 16px 38px rgba(193,127,52,.12);
}
.brand-title { font-size:26px !important; }
/* brand mark: bold filled amber gradient with glow */
.brand-mark {
  width:48px !important; height:48px !important; min-width:48px !important;
  font-size:21px !important; border-radius:13px !important;
  background: linear-gradient(135deg, #C9883A 0%, #A96B27 100%) !important;
  color:#FFFCF8 !important; border:none !important;
  box-shadow: 0 6px 16px rgba(176,110,39,.34), inset 0 1px 0 rgba(255,255,255,.30) !important;
}

/* kicker + chips: a touch of depth, chips lift on hover */
.kicker { box-shadow: 0 1px 2px rgba(44,40,37,.05); }
.chip { box-shadow: 0 1px 2px rgba(44,40,37,.05); transition: transform .15s ease, box-shadow .15s ease; }
.chip:hover { transform: translateY(-1px); box-shadow: 0 4px 10px rgba(44,40,37,.10); }

/* metrics become soft cards */
[data-testid="stMetric"] {
  background: var(--card); border:1px solid var(--line); border-radius:12px;
  padding:.85rem 1rem; box-shadow:0 1px 2px rgba(44,40,37,.04);
  transition: box-shadow .2s ease, transform .2s ease;
}
[data-testid="stMetric"]:hover { transform:translateY(-2px); box-shadow:0 10px 24px rgba(44,40,37,.08); }

/* tabs: selected becomes a soft amber pill, hover tint */
.stTabs [data-baseweb="tab"] { border-radius:10px 10px 0 0; transition:color .15s ease, background .15s ease; }
.stTabs [data-baseweb="tab"]:hover { color:var(--text); background:rgba(193,127,52,.06); }
.stTabs [aria-selected="true"] { background:var(--accent-weak); }

/* inputs: gentle inner depth */
.stTextInput input, .stTextArea textarea, .stNumberInput input,
div[data-baseweb="select"] > div {
  box-shadow: inset 0 1px 2px rgba(44,40,37,.04) !important;
  transition: border-color .15s ease, box-shadow .15s ease;
}

/* dataframe + file-well polish */
[data-testid="stDataFrame"] { box-shadow:0 1px 3px rgba(44,40,37,.05); overflow:hidden; }
[data-testid="stFileUploaderDropzone"] { transition: border-color .15s ease, background .15s ease; }
[data-testid="stFileUploaderDropzone"]:hover { border-color:var(--accent); background:#FFFBF4; }

/* progress bar + spinner in the amber accent */
.stProgress > div > div > div > div { background: linear-gradient(90deg,#C9883A,#B06E27) !important; }
.stSpinner > div { border-top-color: var(--accent) !important; }

/* refined amber scrollbar */
::-webkit-scrollbar { width:11px; height:11px; }
::-webkit-scrollbar-thumb { background:#DCC9A8; border-radius:8px; border:3px solid transparent; background-clip:padding-box; }
::-webkit-scrollbar-thumb:hover { background:#C9883A; background-clip:padding-box; }
::-webkit-scrollbar-track { background:transparent; }
</style>
"""
st.markdown(ENHANCE_CSS, unsafe_allow_html=True)

# ---- visual system v2: bolder, more "product" (still CSS-only, append-only) ----
# A heavier presentational layer on top of the base + enhance themes: roomier
# canvas, segmented tabs, real KPI cards, larger type, framed tables. Restyles
# existing elements only — no markup, no widgets, no logic, no behaviour change.
ENHANCE2_CSS = """
<style>
/* roomier, more confident canvas (purely spatial) */
.block-container { max-width:960px !important; padding-top:2rem !important; }

/* richer page surface: warm wash + faint amber glow */
.stApp {
  background:
    radial-gradient(880px 460px at 88% -10%, rgba(193,127,52,.08) 0%, rgba(193,127,52,0) 58%),
    radial-gradient(1100px 560px at 8% -12%, #FFFFFF 0%, rgba(255,255,255,0) 60%),
    linear-gradient(180deg, #FBF7F0 0%, #F2E8D8 100%) !important;
}

/* headings: larger, tighter; kicker becomes a crisp uppercase tag */
h1, .stApp h1 { font-size:33px !important; letter-spacing:-.012em; margin-bottom:.35rem; }
h2, .stApp h2 { font-size:23px !important; }
.kicker {
  text-transform:uppercase; letter-spacing:.11em; font-size:10.5px; font-weight:600;
  padding:4px 11px;
}
.brand-sub { font-size:14.5px; }

/* cards / expanders / run-status: larger radius, stronger float, warmer edge */
.card, [data-testid="stExpander"], div[data-testid="stStatusWidget"] {
  border-radius:18px !important; border-color:#EADFCD !important;
  box-shadow:0 1px 3px rgba(44,40,37,.05), 0 20px 44px rgba(44,40,37,.085) !important;
}
.card { padding:1.7rem !important; }
[data-testid="stExpander"] summary { font-weight:600; font-size:14.5px; padding:.15rem 0; }

/* KPI cards: bold, confident, gradient-faced */
[data-testid="stMetric"] {
  background:linear-gradient(180deg,#FFFEFB 0%, #FBF5EC 100%) !important;
  border:1px solid #EADFCD !important; border-radius:16px !important;
  padding:1.05rem 1.15rem !important;
  box-shadow:0 1px 2px rgba(44,40,37,.04), 0 12px 26px rgba(44,40,37,.07) !important;
}
[data-testid="stMetric"]:hover { transform:translateY(-3px); box-shadow:0 16px 32px rgba(44,40,37,.11) !important; }
[data-testid="stMetricValue"] { font-size:30px !important; font-weight:600 !important; letter-spacing:-.015em; }
[data-testid="stMetricLabel"] {
  text-transform:uppercase !important; letter-spacing:.07em !important;
  font-size:10.5px !important; font-weight:600 !important; opacity:.85;
}

/* tabs → a clean segmented control */
.stTabs [data-baseweb="tab-list"] {
  gap:.25rem; background:#F1E7D6; border:1px solid #EADFCD; border-bottom:1px solid #EADFCD;
  padding:5px; border-radius:14px; width:fit-content;
}
.stTabs [data-baseweb="tab"] {
  border-radius:10px !important; padding:.5rem 1.15rem !important; font-weight:600 !important;
  background:transparent;
}
.stTabs [data-baseweb="tab"]:hover { background:rgba(193,127,52,.08); color:var(--text); }
.stTabs [aria-selected="true"] {
  background:#FFFFFF !important; color:var(--accent) !important;
  box-shadow:0 1px 3px rgba(44,40,37,.14);
}
.stTabs [data-baseweb="tab-highlight"], .stTabs [data-baseweb="tab-border"] { display:none !important; }

/* buttons: chunkier + rounder (gradient primary already set) */
.stButton > button, .stDownloadButton > button {
  font-weight:600 !important; padding:.62rem 1.3rem !important; border-radius:12px !important;
}

/* inputs: larger, softer, bolder labels, clear focus */
.stTextInput input, .stTextArea textarea, .stNumberInput input,
div[data-baseweb="select"] > div {
  border-radius:12px !important; border-color:#E4D8C5 !important;
}
.stTextInput input, .stNumberInput input { padding:.55rem .75rem !important; }
[data-testid="stWidgetLabel"] p, [data-testid="stWidgetLabel"] label {
  font-weight:600 !important; font-size:13px !important;
}

/* alerts: clean elevated cards (semantic icon stays legible) */
[data-testid="stAlert"], [data-testid="stAlertContainer"], div[role="alert"] {
  border-radius:14px !important; border:1px solid #EADFCD !important;
  box-shadow:0 8px 20px rgba(44,40,37,.07) !important;
}

/* dataframes / editor / iframe: framed, rounded, floating */
[data-testid="stDataFrame"], [data-testid="stDataEditor"] {
  border:1px solid #EADFCD !important; border-radius:14px !important; overflow:hidden;
  box-shadow:0 10px 24px rgba(44,40,37,.06) !important;
}
iframe { border-radius:14px; }

/* softer dividers */
hr { border-top:1px solid #EADFCD !important; opacity:.85; }
</style>
"""
st.markdown(ENHANCE2_CSS, unsafe_allow_html=True)

# ---- visual system v3: enterprise refinement (CSS-only, append-only) ----
# Dials elevation back toward "precise" (less floaty/AI), and adds a few quiet,
# on-brand professional touches. Restyles existing elements only.
ENHANCE3_CSS = """
<style>
/* precise elevation rather than heavy float — reads more enterprise */
.card, [data-testid="stExpander"], div[data-testid="stStatusWidget"] {
  box-shadow: 0 1px 2px rgba(44,40,37,.05), 0 8px 20px rgba(44,40,37,.06) !important;
}
.brand { box-shadow: 0 1px 3px rgba(44,40,37,.05), 0 10px 26px rgba(193,127,52,.085) !important; }

/* confident section subheaders with a quiet hairline */
.stApp h3 { padding-bottom:.3rem; border-bottom:1px solid #ECE3D3; }

/* on-brand select / multiselect chips */
[data-baseweb="tag"] {
  background: var(--accent-weak) !important; color: var(--accent) !important;
  border:1px solid var(--line) !important;
}

/* on-brand radio / checkbox marks */
[data-baseweb="radio"] [aria-checked="true"], [data-baseweb="checkbox"] [aria-checked="true"] {
  background-color: var(--accent) !important; border-color: var(--accent) !important;
}

/* calmer captions, clearer file drop target */
[data-testid="stCaptionContainer"], .stCaption { line-height:1.65; }
[data-testid="stFileUploaderDropzone"] { padding:1.1rem 1.25rem; }

/* primary CTA: tighter, confident */
.stButton > button[kind="primary"] { letter-spacing:.005em; }
</style>
"""
st.markdown(ENHANCE3_CSS, unsafe_allow_html=True)


# ============================================================ session helpers
def _ss(key, default):
    if key not in st.session_state:
        st.session_state[key] = default
    return st.session_state[key]


CURRENT_USER = require_authenticated_user(st)
PROJECT_ID, PROJECT_PATHS = ensure_project_workspace(st.session_state)
PROJECT_STORE = get_project_store()


def _log(phase: str, event: str, success: bool, *, metrics=None, details=None, error: str | None = None):
    PROJECT_STORE.audit_event(
        PROJECT_ID, CURRENT_USER, phase, event, success,
        details={"metrics": metrics or {}, "details": details or {}, "error": error or ""},
    )
    return log_event(
        PROJECT_ID, phase, event, success,
        provider=st.session_state.get("api_provider"),
        model=st.session_state.get("api_model"),
        metrics=metrics, details=details, error=error,
        log_dir=PROJECT_PATHS["logs"],
    )


def _project_metadata() -> dict:
    study = st.session_state.get("p2_study", {}) or {}
    return {
        "client_name": study.get("client_name", ""),
        "study_title": study.get("study_title", ""),
        "project_id": PROJECT_ID,
        "owner": CURRENT_USER,
    }


def _set_project_status(status: str):
    PROJECT_STORE.set_status(PROJECT_ID, status, owner=CURRENT_USER, metadata=_project_metadata())
    st.session_state["project_status"] = status


def _record_artifact(artifact_type: str, path: str, metadata=None):
    version = PROJECT_STORE.record_artifact(PROJECT_ID, artifact_type, path, metadata=metadata or {})
    _log("app", "artifact_recorded", True, metrics={"artifact_type": artifact_type, "version": version}, details={"filename": os.path.basename(path)})
    return version


def _show_error(title: str, exc: BaseException, phase: str, event: str):
    msg = friendly_error_message(exc)
    st.error(f"{title}. {msg}")
    with st.expander("Show technical details", expanded=False):
        st.code(technical_error_details(exc), language="text")
    _log(phase, event, False, details={"error_type": exc.__class__.__name__}, error=msg)


def _upload_file_tuples(uploaded_files):
    files = uploaded_files if isinstance(uploaded_files, list) else [uploaded_files]
    return [(getattr(f, "name", "uploaded_file"), getattr(f, "size", None)) for f in files if f is not None]


def _validate_uploads(uploaded_files, allowed_exts, *, phase: str, event: str) -> bool:
    errors = validate_uploaded_files(_upload_file_tuples(uploaded_files), allowed_exts)
    if errors:
        for err in errors:
            st.error(err)
        _log(phase, event, False, metrics={"error_count": len(errors)}, error="; ".join(errors))
        return False
    return True


def _record_cleanup_once():
    if not _ss("cleanup_ran", False):
        removed = cleanup_old_project_dirs(exclude_project_id=PROJECT_ID)
        st.session_state["cleanup_ran"] = True
        _log("app", "temp_cleanup", True,
             metrics={"removed_project_folders": len(removed),
                      "retention_hours": DEFAULT_RETENTION_HOURS})


def _record_session_once():
    if not _ss("session_init_logged", False):
        st.session_state["session_init_logged"] = True
        _log("app", "session_initialized", True,
             details={"project_folder": str(PROJECT_PATHS["base"]),
                      "max_upload_mb": MAX_UPLOAD_MB,
                      "max_total_upload_mb": MAX_TOTAL_UPLOAD_MB})


def _write_intermediate_json(name: str, payload) -> str:
    version = PROJECT_STORE.next_artifact_version(PROJECT_ID, name)
    path = unique_path(PROJECT_PATHS["intermediate"], f"{safe_filename(name)}_v{version:03d}_{PROJECT_ID[:8]}.json")
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    PROJECT_STORE.record_artifact(PROJECT_ID, name, path, version=version, metadata={"kind": "intermediate_json"})
    PROJECT_STORE.save_snapshot(PROJECT_ID, {"artifact": name, "payload": payload}, status=st.session_state.get("project_status", "Draft"))
    return str(path)


_ss("api_provider", PROVIDERS[0])
_ss("api_key", "")
_ss("api_model", DEFAULT_MODELS[PROVIDERS[0]][0])
_ss("conn_status", None)
_ss("p2_sections", None)
_ss("p2_result", None)
_ss("p2_editor_rows", None)
_ss("p2_spec_path", None)
_ss("p2_export_override", False)
_ss("p2_final_approved", False)
_ss("p2_artifact_path", None)
_ss("p2_usage", None)
_ss("p2_hyps", [])  # blank by default; filled from Phase 1 hand-off or an upload
_ss("hyp_upload_sig", None)
# Blank fields by default, but keep each field's type (frameworks is a list, not a str)
_ss("p2_study", {k: ("" if isinstance(v, str) else ([] if isinstance(v, list) else v))
                 for k, v in SAMPLE_STUDY.model_dump().items()})
_ss("p2_source", "")  # "phase1" once hypotheses arrive from Phase 1; "upload" if uploaded
_ss("project_status", "Draft")
# Phase 1 state
_ss("p1_brief", StudyBrief(study_type="Segmentation", frameworks=["HCP · PACE-B Framework"]).model_dump())
_ss("p1_docs", [])           # list of SourceDoc dicts
_ss("p1_upload_sig", None)
_ss("p1_clars", None)        # clarifying questions (list of dicts)
_ss("p1_hyps", None)         # generated hypotheses (list of dicts)
_ss("p1_generation_fallback_used", False)
_ss("p1_last_generation_error", "")

_record_cleanup_once()
PROJECT_STORE.ensure_project(PROJECT_ID, owner=CURRENT_USER, status=st.session_state.get("project_status", "Draft"), metadata=_project_metadata())
_record_session_once()


def get_client() -> LLMClient | None:
    if not st.session_state["api_key"]:
        return None
    return LLMClient(
        provider=st.session_state["api_provider"],
        api_key=st.session_state["api_key"],
        model=st.session_state["api_model"],
    )


def parse_hypotheses_upload(uploaded) -> list[dict]:
    """
    Parse uploaded hypotheses from Phase 1 export/workbooks, CSV, TXT, or MD.
    If a review/status column is present, rejected rows are skipped.
    """
    name = (uploaded.name or "").lower()
    rows: list[dict] = []

    def _split_sources(value):
        import re as _re
        if isinstance(value, list):
            return [str(v).strip() for v in value if str(v).strip()]
        return [v.strip() for v in _re.split(r"[;,\n]+", str(value or "")) if v.strip()]

    def _norm(hid, txt, cat="", seg="All", stype="", **extra):
        status = str(extra.get("review_status", extra.get("status", "Approved")) or "Approved").strip()
        if status and status.lower() != "approved":
            return None
        return {
            "hypothesis_id": str(hid).strip(),
            "text": str(txt).strip(),
            "category": str(cat).strip(),
            "target_segment": str(seg).strip() or "All",
            "study_type": str(stype).strip(),
            "business_question": str(extra.get("business_question", "") or ""),
            "audience": str(extra.get("audience", "") or ""),
            "priority": str(extra.get("priority", "Medium") or "Medium"),
            "review_status": "Approved",
            "review_notes": str(extra.get("review_notes", extra.get("notes", "")) or ""),
            "source_files": _split_sources(extra.get("source_files", extra.get("source_filenames", ""))),
            "source_excerpt": str(extra.get("source_excerpt", extra.get("source_refs", "")) or ""),
            "validation_warnings": _split_sources(extra.get("validation_warnings", "")),
            "duplicate_of": str(extra.get("duplicate_of", "") or ""),
        }

    if name.endswith(".xlsx"):
        import openpyxl, io
        wb = openpyxl.load_workbook(io.BytesIO(uploaded.getvalue()), read_only=True, data_only=True)
        ws = wb["Hypotheses"] if "Hypotheses" in wb.sheetnames else wb.active
        data = [[("" if c is None else str(c)).strip() for c in r] for r in ws.iter_rows(values_only=True)]
        if not data:
            return []
        header = [h.lower() for h in data[0]]
        def col(*names):
            for n in names:
                if n in header:
                    return header.index(n)
            return None
        i_id = col("hypothesis_id", "id", "hyp_id", "h_id", "hypothesis number", "hypotheses number", "hypothesis_number", "hypotheses_number")
        i_txt = col("text", "hypothesis", "hypotheses", "hypothesis_text", "statement", "claim")
        i_cat = col("category", "type", "framework")
        i_seg = col("target_segment", "segment")
        i_st = col("study_type")
        i_status = col("review_status", "status")
        i_sources = col("source_files", "source_filenames")
        i_excerpt = col("source_excerpt", "source_refs")
        i_bq = col("business_question")
        i_aud = col("audience")
        i_pri = col("priority")
        i_notes = col("review_notes", "notes")
        i_warn = col("validation_warnings")
        i_dup = col("duplicate_of")
        if i_txt is None and i_id is None:
            for k, r in enumerate(data, start=1):
                if r and r[0]:
                    row = _norm(f"H{k:03d}", r[0])
                    if row:
                        rows.append(row)
            return rows
        def val(r, idx, default=""):
            return r[idx] if (idx is not None and idx < len(r)) else default
        for k, r in enumerate(data[1:], start=1):
            txt = val(r, i_txt, "")
            if not txt:
                continue
            row = _norm(
                val(r, i_id, f"H{k:03d}") or f"H{k:03d}", txt,
                val(r, i_cat, ""), val(r, i_seg, "All"), val(r, i_st, ""),
                review_status=val(r, i_status, "Approved"),
                source_files=val(r, i_sources, ""),
                source_excerpt=val(r, i_excerpt, ""),
                business_question=val(r, i_bq, ""),
                audience=val(r, i_aud, ""),
                priority=val(r, i_pri, "Medium"),
                review_notes=val(r, i_notes, ""),
                validation_warnings=val(r, i_warn, ""),
                duplicate_of=val(r, i_dup, ""),
            )
            if row:
                rows.append(row)
        return rows

    if name.endswith(".csv"):
        import csv, io
        text = uploaded.getvalue().decode("utf-8-sig", errors="replace")
        reader = csv.DictReader(io.StringIO(text))
        if reader.fieldnames:
            lower_map = {f.lower(): f for f in reader.fieldnames}
            def field(*names):
                for n in names:
                    if n in lower_map:
                        return lower_map[n]
                return None
            f_id = field("hypothesis_id", "id", "hyp_id", "hypothesis number", "hypotheses number", "hypothesis_number", "hypotheses_number")
            f_txt = field("text", "hypothesis", "hypotheses", "hypothesis_text", "statement", "claim")
            f_cat = field("category", "type", "framework")
            f_seg = field("target_segment", "segment")
            f_st = field("study_type")
            f_status = field("review_status", "status")
            f_sources = field("source_files", "source_filenames")
            f_excerpt = field("source_excerpt", "source_refs")
            f_bq = field("business_question")
            f_aud = field("audience")
            f_pri = field("priority")
            f_notes = field("review_notes", "notes")
            f_warn = field("validation_warnings")
            f_dup = field("duplicate_of")
            for k, row in enumerate(reader, start=1):
                txt = (row.get(f_txt) if f_txt else "") or ""
                if not txt.strip():
                    if not f_txt and reader.fieldnames:
                        txt = row.get(reader.fieldnames[0], "")
                    if not txt.strip():
                        continue
                normed = _norm(
                    (row.get(f_id) if f_id else "") or f"H{k:03d}", txt,
                    row.get(f_cat, "") if f_cat else "",
                    row.get(f_seg, "All") if f_seg else "All",
                    row.get(f_st, "") if f_st else "",
                    review_status=row.get(f_status, "Approved") if f_status else "Approved",
                    source_files=row.get(f_sources, "") if f_sources else "",
                    source_excerpt=row.get(f_excerpt, "") if f_excerpt else "",
                    business_question=row.get(f_bq, "") if f_bq else "",
                    audience=row.get(f_aud, "") if f_aud else "",
                    priority=row.get(f_pri, "Medium") if f_pri else "Medium",
                    review_notes=row.get(f_notes, "") if f_notes else "",
                    validation_warnings=row.get(f_warn, "") if f_warn else "",
                    duplicate_of=row.get(f_dup, "") if f_dup else "",
                )
                if normed:
                    rows.append(normed)
        return rows

    text = uploaded.getvalue().decode("utf-8-sig", errors="replace")
    k = 0
    for ln in text.splitlines():
        ln = ln.strip().lstrip("-•* ").strip()
        if not ln:
            continue
        k += 1
        if ":" in ln and len(ln.split(":", 1)[0]) <= 12:
            hid, txt = ln.split(":", 1)
            row = _norm(hid, txt)
        else:
            row = _norm(f"H{k:03d}", ln)
        if row:
            rows.append(row)
    return rows

# ============================================================ header
prov = st.session_state["api_provider"]
if st.session_state["conn_status"] == "ok":
    chip = '<span class="chip chip-ok">● Connected</span>'
elif st.session_state["conn_status"] == "fail":
    chip = '<span class="chip chip-warn">● Connection failed</span>'
elif st.session_state["api_key"]:
    chip = '<span class="chip">● Key set — untested</span>'
else:
    chip = '<span class="chip chip-off">● No API key</span>'

st.markdown(
    '<div class="brand">'
    '<div class="brand-mark">◆</div>'
    '<div class="brand-text">'
    '<div class="brand-title">Hypothesis-driven survey generator</div>'
    '<div class="brand-sub">Hypotheses → clean survey outline → formatted questionnaire</div>'
    '</div></div>'
    f'<div class="status-row">{chip}<span class="chip chip-off">{prov}</span><span class="chip chip-off">Project {PROJECT_ID[:8]}</span><span class="chip chip-off">{CURRENT_USER}</span><span class="chip chip-ok">{st.session_state.get("project_status", "Draft")}</span></div>',
    unsafe_allow_html=True)


with st.expander("Project status, persistence & audit", expanded=False):
    rec = PROJECT_STORE.project_record(PROJECT_ID) or {}
    c1, c2, c3 = st.columns(3)
    c1.metric("Project ID", PROJECT_ID[:8])
    c2.metric("Status", rec.get("status", st.session_state.get("project_status", "Draft")))
    c3.metric("Owner", CURRENT_USER)
    st.caption(f"Persistent project database: {PROJECT_STORE.db_path}")
    st.download_button(
        "Download audit trail (.jsonl)",
        PROJECT_STORE.export_audit_jsonl(PROJECT_ID),
        file_name=f"audit_{PROJECT_ID[:8]}.jsonl",
        mime="application/x-ndjson",
        use_container_width=True,
        key="download_audit_trail_button",
    )



# ============================================================ API config panel
with st.expander("API configuration — provider, key & model (powers phases 1 & 2)",
                 expanded=not bool(st.session_state["api_key"])):
    a, b, c = st.columns([1, 1.4, 1])
    with a:
        provider = st.selectbox("Provider", PROVIDERS,
                                index=PROVIDERS.index(st.session_state["api_provider"]),
                                key="api_provider_select")
        if provider != st.session_state["api_provider"]:
            st.session_state["api_provider"] = provider
            st.session_state["api_model"] = DEFAULT_MODELS[provider][0]
            st.session_state["conn_status"] = None
            st.rerun()
    with b:
        st.session_state["api_key"] = st.text_input(
            "API key", value=st.session_state["api_key"], type="password",
            placeholder="sk-…  (kept in session memory only, never saved)",
            key="api_key_input")
    with c:
        models = DEFAULT_MODELS[provider]
        model_choice = st.selectbox("Model", models + ["Custom…"],
                                    index=models.index(st.session_state["api_model"])
                                    if st.session_state["api_model"] in models else 0,
                                    key="api_model_select")
        if model_choice == "Custom…":
            st.session_state["api_model"] = st.text_input(
                "Custom model string", value=st.session_state["api_model"], key="api_custom_model_input")
        else:
            st.session_state["api_model"] = model_choice

    tcol, scol = st.columns([1, 4])
    with tcol:
        if st.button("Test connection", use_container_width=True, key="api_test_connection_button"):
            client = get_client()
            if not client:
                st.session_state["conn_status"] = "fail"
                st.warning("Enter an API key first.")
                _log("app", "connection_test", False, error="No API key provided")
            else:
                start = perf_counter()
                ok, msg = client.test_connection()
                st.session_state["conn_status"] = "ok" if ok else "fail"
                _log("app", "connection_test", ok,
                     metrics={"latency_ms": int((perf_counter() - start) * 1000)},
                     error=None if ok else msg)
                (st.success if ok else st.error)(
                    f"{'Connected — ' if ok else 'Failed: '}{msg}")
    with scol:
        st.caption("API keys are stored only in the active Streamlit server session "
                   "and are never written to disk or logged by this app.")

    st.info("Privacy note: uploaded files, extracted text, hypotheses, and generated survey "
            "content are sent to the selected LLM provider during Phases 1 and 2. "
            "Use a provider/account approved for your client data.")


# ============================================================ tabs
tab1, tab2, tab3 = st.tabs([
    "1 · Hypothesis generation",
    "2 · Survey outline (AI)",
    "3 · Questionnaire document",
])

# Auto-switch to the Phase 2 tab after "Approve & send to Phase 2" (Streamlit has no
# Python API to select a tab, so click it client-side; no-ops if not found).
if st.session_state.pop("_goto_phase2", False):
    components.html(
        """<script>
        const doc = window.parent.document;
        const go = () => {
            const tabs = doc.querySelectorAll('button[role="tab"]');
            if (tabs.length > 1) { tabs[1].click(); } else { setTimeout(go, 120); }
        };
        setTimeout(go, 120);
        </script>""",
        height=0,
    )


# ------------------------------------------------------------ PHASE 1
with tab1:
    st.markdown('<div class="kicker">Phase 1 · hypothesis generation</div>', unsafe_allow_html=True)
    st.subheader("Generate hypotheses from your files and context")
    st.caption("Upload your source material, describe the study, pick frameworks, "
               "answer a few clarifying questions, then generate hypotheses that "
               "flow into Phase 2.")

    no_key = not bool(st.session_state["api_key"])
    if no_key:
        st.caption("Set an API key in the configuration panel above to run Phase 1.")

    brief = st.session_state["p1_brief"]

    # ---- 1. files ----
    st.markdown("##### 1 · Source material")
    p1_files = st.file_uploader(
        "Drop in briefs, decks, data or notes (PDF, DOCX, PPTX, XLSX, CSV, TXT, MD)",
        type=SUPPORTED_SOURCE_EXTENSIONS,
        accept_multiple_files=True, key="p1_uploader")
    if p1_files:
        if _validate_uploads(
            p1_files, SUPPORTED_SOURCE_EXTENSIONS, phase="phase1", event="source_upload_validation"
        ):
            sig = ";".join(f"{f.name}:{f.size}" for f in p1_files)
            if st.session_state["p1_upload_sig"] != sig:
                start = perf_counter()
                try:
                    payloads = []
                    with st.spinner("Reading files…"):
                        for f in p1_files:
                            data = f.getvalue()
                            write_bytes_unique(PROJECT_PATHS["uploads"], f.name, data, prefix="phase1")
                            payloads.append((f.name, data))
                        docs = p1_extract.extract_many(payloads)
                    st.session_state["p1_docs"] = [d.model_dump() for d in docs]
                    st.session_state["p1_upload_sig"] = sig
                    _log(
                        "phase1", "source_extraction", True,
                        metrics={
                            "file_count": len(p1_files),
                            "total_bytes": sum(int(getattr(f, "size", 0) or 0) for f in p1_files),
                            "extracted_chars": sum(d.original_char_count for d in docs),
                            "prompt_chars": sum(d.extracted_char_count or len(d.text or "") for d in docs),
                            "warning_count": sum(len(d.warnings) for d in docs),
                            "error_count": sum(len(d.errors) for d in docs),
                            "weak_file_count": sum(1 for d in docs if d.weak_extraction),
                            "latency_ms": int((perf_counter() - start) * 1000),
                        },
                    )
                except Exception as e:  # noqa: BLE001
                    _show_error("File extraction failed", e, "phase1", "source_extraction")
        else:
            st.session_state["p1_docs"] = []
            st.session_state["p1_upload_sig"] = None
    docs_state = st.session_state["p1_docs"]
    if docs_state:
        docs_obj = [SourceDoc.model_validate(d) for d in docs_state]
        weak_count = sum(1 for d in docs_obj if d.weak_extraction)
        err_count = sum(len(d.errors) for d in docs_obj)
        warn_count = sum(len(d.warnings) for d in docs_obj)

        summary_rows = p1_extract.extraction_summary_rows(docs_obj)
        summary_cols = [
            "Filename", "Type", "Processed", "Extracted chars", "Prompt chars",
            "Truncated", "Weak extraction", "Warning count", "Error count", "Issue summary",
        ]
        st.dataframe(
            [{c: row.get(c, "") for c in summary_cols} for row in summary_rows],
            use_container_width=True,
            hide_index=True,
        )
        if weak_count or err_count:
            st.warning(
                f"{weak_count} file(s) have weak extraction and {err_count} parsing error(s). "
                "You can still continue if the extracted-text preview looks usable."
            )
        elif warn_count:
            st.info(f"Extraction completed with {warn_count} warning(s). You can continue if the preview looks usable.")

        issue_rows = p1_extract.extraction_issue_rows(docs_obj)
        if issue_rows:
            with st.expander(f"Show extraction warning/error details ({warn_count} warning(s), {err_count} error(s))", expanded=False):
                st.dataframe(issue_rows, use_container_width=True, hide_index=True)

        with st.expander("Preview extracted text from uploaded files", expanded=False):
            for i, d in enumerate(docs_obj):
                st.markdown(f"**{d.filename}** — {d.kind}, {d.extracted_character_count:,} chars used")
                st.caption(shorten_text(d.note or "", 280) if d.note else "")
                st.text_area(
                    "Extracted text",
                    value=d.text or "(no extractable text)",
                    height=180,
                    key=f"p1_extract_preview_{i}_{d.filename}",
                    label_visibility="collapsed",
                    disabled=True,
                )

        dl1, dl2 = st.columns(2)
        with dl1:
            st.download_button(
                "Download extraction report (.md)",
                p1_extract.extraction_report_markdown(docs_obj),
                file_name=f"extraction_report_{PROJECT_ID[:8]}.md",
                mime="text/markdown",
                use_container_width=True,
                key="p1_download_extraction_report_md_button",
            )
        with dl2:
            try:
                extraction_xlsx = p1_extract.extraction_report_xlsx_bytes(docs_obj)
            except Exception as e:  # noqa: BLE001
                extraction_xlsx = None
                st.warning("The XLSX extraction report could not be prepared. You can still continue and use the Markdown report.")
                with st.expander("Show extraction-report export details", expanded=False):
                    st.code(technical_error_details(e), language="text")
                _log("phase1", "extraction_report_xlsx", False, details={"error_type": e.__class__.__name__}, error=friendly_error_message(e))
            if extraction_xlsx:
                st.download_button(
                    "Download extraction report (.xlsx)",
                    extraction_xlsx,
                    file_name=f"extraction_report_{PROJECT_ID[:8]}.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    use_container_width=True,
                    key="p1_download_extraction_report_xlsx_button",
                )

    # ---- 2. brief ----
    st.markdown("##### 2 · Study brief")
    brief["goal"] = st.text_area("What is your goal for this study?", brief.get("goal", ""),
                                 height=80, placeholder="e.g. Understand what drives adoption "
                                 "of newer biologics so we can build a segmentation.",
                                 key="p1_goal_text_area")
    bc1, bc2 = st.columns(2)
    study_type_opts = ["Segmentation", "ATU (Awareness, Trial & Usage)", "Conjoint / DCE",
                       "DTC / Patient", "Message testing", "Pricing", "Other"]
    cur_type = brief.get("study_type", "Segmentation")
    idx = study_type_opts.index(cur_type) if cur_type in study_type_opts else len(study_type_opts) - 1
    brief["study_type"] = bc1.selectbox("Study type", study_type_opts, index=idx, key="p1_study_type_select")
    brief["disease_area"] = bc2.text_input("Disease area", brief.get("disease_area", ""), key="p1_disease_area_input")
    bc3, bc4 = st.columns(2)
    brief["target_audience"] = bc3.text_input("Target audience", brief.get("target_audience", ""),
                                              placeholder="e.g. Rheumatologists", key="p1_target_audience_input")
    brief["market"] = bc4.text_input("Market / geography", brief.get("market", ""),
                                     placeholder="e.g. US", key="p1_market_input")
    brief["key_decisions"] = st.text_input(
        "What decisions must this study inform?", brief.get("key_decisions", ""),
        placeholder="e.g. Which segments to target and with what message", key="p1_key_decisions_input")
    brief["survey_length"] = st.text_input(
        "Length of survey (including the screener)", brief.get("survey_length", ""),
        placeholder="e.g. 30 minutes", key="p1_survey_length_input",
        help="Target interview length. The generator sizes the number of sections and "
             "questions to fit — works for short or long surveys of any type.")
    brief["extra_context"] = st.text_area("Any other context", brief.get("extra_context", ""),
                                          height=70, key="p1_extra_context_text_area")
    brief["client_name"] = st.text_input("Client", brief.get("client_name", "Demo Client"), key="p1_client_name_input")

    # ---- 3. frameworks ----
    st.markdown("##### 3 · Framework blocks")
    st.caption(
        "Select one or more framework blocks. In the hypothesis CSV, `category` will be a specific "
        "construct/dimension from the selected block (for example Potential, Attitudes, Access barriers), "
        "not the framework name itself."
    )

    current_frameworks = set(brief.get("frameworks") or [])
    if not current_frameworks or current_frameworks == {"Drivers & barriers"}:
        current_frameworks = {"HCP · PACE-B Framework"}

    selected_frameworks = []
    for group_name, group_items in FRAMEWORK_LIBRARY.items():
        with st.expander(group_name, expanded=(group_name == "HCP Frameworks")):
            for fw in group_items:
                display_name = str(fw["display_name"])
                with st.container(border=True):
                    checked = st.checkbox(
                        str(fw["name"]),
                        value=display_name in current_frameworks,
                        key=f"p1_framework_block_{fw['id']}",
                    )
                    st.caption(str(fw.get("summary", "")))
                    st.caption("Category examples: " + ", ".join(str(c) for c in fw.get("categories", [])[:8]))
                    if checked:
                        selected_frameworks.append(display_name)

    custom_selected_default = bool(brief.get("custom_framework_prompt")) or CUSTOM_FRAMEWORK_NAME in current_frameworks
    custom_enabled = st.checkbox(
        "Custom framework",
        value=custom_selected_default,
        key="p1_custom_framework_checkbox",
        help="Use this when your segmentation framework is specific to a client, brand, or methodology.",
    )
    if custom_enabled:
        brief["custom_framework_prompt"] = st.text_area(
            "Custom framework prompt",
            value=brief.get("custom_framework_prompt", ""),
            height=180,
            placeholder=(
                "Describe the framework, the dimensions/categories the model should use, "
                "and examples of the hypotheses it should create."
            ),
            key="p1_custom_framework_prompt_text_area",
        )
        if brief.get("custom_framework_prompt", "").strip():
            selected_frameworks.append(CUSTOM_FRAMEWORK_NAME)
    else:
        brief["custom_framework_prompt"] = ""

    if not selected_frameworks:
        st.warning("Select at least one framework block or add a custom framework before generating hypotheses.")
    else:
        cats_preview = category_suggestions(selected_frameworks, brief.get("custom_framework_prompt", ""))[:18]
        if cats_preview:
            st.caption("Category suggestions from selected blocks: " + ", ".join(cats_preview))

    brief["frameworks"] = selected_frameworks
    st.session_state["p1_brief"] = brief
    no_frameworks = not bool(selected_frameworks)

    st.divider()

    # ---- 4. clarify ----
    st.markdown("##### 4 · Clarify")
    if st.button("Read files & ask clarifying questions", type="primary", disabled=(no_key or no_frameworks), key="p1_clarifier_button"):
        client = get_client()
        start = perf_counter()
        try:
            brief_obj = StudyBrief.model_validate(st.session_state["p1_brief"])
            docs_obj = [SourceDoc.model_validate(d) for d in st.session_state["p1_docs"]]
            with st.status("Reading material…", expanded=True) as status:
                def prog(step, st_, detail):
                    status.write(f"**{step}** — {detail}")
                clars = p1_pipeline.run_clarifier(client, brief_obj, docs_obj, prog)
                st.session_state["p1_clars"] = [c.model_dump() for c in clars]
                status.update(label=f"{len(clars)} clarifying questions", state="complete")
                _log("phase1", "clarifier", True,
                     metrics={"question_count": len(clars),
                              "source_file_count": len(docs_obj),
                              "latency_ms": int((perf_counter() - start) * 1000)})
        except Exception as e:  # noqa: BLE001
            _show_error("Clarifier failed", e, "phase1", "clarifier")

    if st.session_state["p1_clars"]:
        st.caption("Confirm or edit the suggested answers — they guide generation.")
        for i, c in enumerate(st.session_state["p1_clars"]):
            st.markdown(f"**{c['question']}**")
            if c.get("why"):
                st.caption(c["why"])
            c["answer"] = st.text_area(
                "Answer", c.get("answer", c.get("suggested_answer", "")),
                key=f"p1_clar_ans_{i}", label_visibility="collapsed", height=90)
        st.session_state["p1_clars"] = st.session_state["p1_clars"]

    # ---- 5. generate ----
    st.markdown("##### 5 · Generate hypotheses")
    with st.expander("Hypothesis output contract / diagnostics", expanded=False):
        st.caption(
            "Phase 1 now asks the model for a simple four-column CSV instead of JSON. "
            "This avoids the earlier failure mode where the model returned a valid-looking answer, "
            "but not in the exact JSON shape the parser expected."
        )
        st.code(csv_example_rows(category_suggestions(brief.get("frameworks") or [], brief.get("custom_framework_prompt", ""))), language="csv")
        st.caption(
            f"Required header: {canonical_header_line()}. "
            "If the model still returns malformed output, the app retries with stricter CSV instructions and then creates editable starter rows so you can continue."
        )
    gen_label = "Regenerate hypotheses" if st.session_state["p1_hyps"] else "Generate hypotheses"
    feedback = ""
    if st.session_state["p1_hyps"]:
        feedback = st.text_input("Optional feedback for regeneration",
                                 placeholder="e.g. Add more barrier hypotheses; sharpen H003.",
                                 key="p1_regeneration_feedback_input")
    if st.button(gen_label, type="primary", disabled=(no_key or no_frameworks), key="p1_generate_hypotheses_button"):
        client = get_client()
        st.session_state["p1_generation_fallback_used"] = False
        st.session_state["p1_last_generation_error"] = ""
        start = perf_counter()
        try:
            brief_obj = StudyBrief.model_validate(st.session_state["p1_brief"])
            docs_obj = [SourceDoc.model_validate(d) for d in st.session_state["p1_docs"]]
            answers = [{"question": c["question"], "answer": c.get("answer", "")}
                       for c in (st.session_state["p1_clars"] or [])]
            if feedback.strip():
                answers.append({"question": "Analyst feedback on the previous draft",
                                "answer": feedback.strip()})
            with st.status("Generating hypotheses…", expanded=True) as status:
                def prog(step, st_, detail):
                    status.write(f"**{step}** — {detail}")
                hyps = p1_pipeline.run_generator(client, brief_obj, docs_obj, answers, prog)
                st.session_state["p1_hyps"] = [h.model_dump() for h in hyps]
                st.session_state["p1_validation_issues"] = []
                status.update(label=f"{len(hyps)} hypotheses generated", state="complete")
                _set_project_status("Hypotheses Generated")
                _log("phase1", "hypothesis_generation", True,
                     metrics={"hypothesis_count": len(hyps),
                              "source_file_count": len(docs_obj),
                              "latency_ms": int((perf_counter() - start) * 1000)})
        except Exception as e:  # noqa: BLE001
            st.session_state["p1_last_generation_error"] = friendly_error_message(e)
            _log("phase1", "hypothesis_generation", False,
                 details={"error_type": e.__class__.__name__, "expected_output": canonical_header_line()},
                 error=st.session_state["p1_last_generation_error"])
            try:
                brief_obj = StudyBrief.model_validate(st.session_state["p1_brief"])
                docs_obj = [SourceDoc.model_validate(d) for d in st.session_state["p1_docs"]]
                fallback_rows = p1_pipeline.starter_hypotheses_from_context(brief_obj, docs_obj)
                if fallback_rows:
                    st.session_state["p1_hyps"] = fallback_rows
                    st.session_state["p1_validation_issues"] = [
                        {
                            "severity": "warning",
                            "message": "Editable starter hypotheses were created because AI generation did not return the required four-column CSV. Review and edit before approving.",
                        }
                    ]
                    st.session_state["p1_generation_fallback_used"] = True
                    _log("phase1", "hypothesis_generation_fallback", True,
                         metrics={"hypothesis_count": len(fallback_rows)},
                         error=st.session_state["p1_last_generation_error"])
                    st.warning(
                        "The AI output did not match the required CSV format, so editable starter rows were created. "
                        "You can still proceed after reviewing, editing, and marking rows Approved."
                    )
                    with st.expander("Show generation diagnostics", expanded=False):
                        st.caption("Expected output contract")
                        st.code(csv_example_rows(category_suggestions(brief_obj.frameworks, brief_obj.custom_framework_prompt)), language="csv")
                        st.caption("Technical parser/provider detail")
                        st.code(technical_error_details(e), language="text")
                else:
                    _show_error("Hypothesis generation failed", e, "phase1", "hypothesis_generation")
            except Exception as fallback_exc:  # noqa: BLE001
                _log("phase1", "hypothesis_generation_fallback", False,
                     details={"error_type": fallback_exc.__class__.__name__},
                     error=friendly_error_message(fallback_exc))
                _show_error("Hypothesis generation failed", e, "phase1", "hypothesis_generation")

    if not st.session_state["p1_hyps"]:
        with st.expander("Need to proceed without AI-generated hypotheses?", expanded=False):
            st.caption("Create an editable starter table, then fill in and approve the hypotheses manually.")
            if st.button("Create editable starter hypothesis table", key="p1_create_manual_hypothesis_table_button"):
                brief_obj = StudyBrief.model_validate(st.session_state["p1_brief"])
                docs_obj = [SourceDoc.model_validate(d) for d in st.session_state["p1_docs"]]
                st.session_state["p1_hyps"] = p1_pipeline.starter_hypotheses_from_context(brief_obj, docs_obj)
                st.session_state["p1_generation_fallback_used"] = True
                st.session_state["p1_last_generation_error"] = "Manual starter table created by user."
                st.rerun()

    # ---- 6. review / edit / hand off ----
    if st.session_state["p1_hyps"]:
        st.markdown("##### 6 · Review & approve")
        if st.session_state.get("p1_generation_fallback_used"):
            st.warning("These are starter/fallback rows. Review, edit, and explicitly mark rows Approved before sending to Phase 2.")
        st.caption("Edit hypotheses, traceability, priority, and approval status. Only rows marked Approved are sent to Phase 2.")

        brief_obj_for_edit = StudyBrief.model_validate(st.session_state["p1_brief"])
        docs_obj_for_edit = [SourceDoc.model_validate(d) for d in st.session_state.get("p1_docs", [])]
        editor_rows = p1_pipeline.editor_rows_from_hypotheses(st.session_state["p1_hyps"])

        edited = st.data_editor(
            editor_rows,
            num_rows="dynamic",
            use_container_width=True,
            disabled=["validation_warnings", "duplicate_of"],
            column_config={
                "hypothesis_id": st.column_config.TextColumn("ID", width="small"),
                "text": st.column_config.TextColumn("Hypothesis", width="large"),
                "category": st.column_config.TextColumn(
                    "Category / construct",
                    help="Use a specific construct or dimension, not the framework name.",
                    width="medium"),
                "rationale": st.column_config.TextColumn("Rationale", width="large"),
                "business_question": st.column_config.TextColumn("Business question", width="large"),
                "audience": st.column_config.TextColumn("Audience", width="medium"),
                "priority": st.column_config.SelectboxColumn(
                    "Priority", options=p1_pipeline.PRIORITY_OPTIONS, width="small"),
                "review_status": st.column_config.SelectboxColumn(
                    "Status", options=p1_pipeline.REVIEW_STATUS_OPTIONS, width="medium"),
                "review_notes": st.column_config.TextColumn("Review notes", width="large"),
                "source_files": st.column_config.TextColumn("Source files", width="medium"),
                "source_excerpt": st.column_config.TextColumn("Source excerpt", width="large"),
                "validation_warnings": st.column_config.TextColumn("Validation warnings", width="large"),
                "duplicate_of": st.column_config.TextColumn("Duplicate of", width="small"),
                "study_type": None,
                "target_segment": None,
            },
            key="p1_hyp_editor",
        )

        normalised_hyps, validation_issues = p1_pipeline.normalise_editor_rows(edited, brief_obj_for_edit, docs_obj_for_edit)
        st.session_state["p1_hyps"] = normalised_hyps

        approved_count = sum(1 for h in normalised_hyps if h.get("review_status") == "Approved" and h.get("text"))
        needs_edit_count = sum(1 for h in normalised_hyps if h.get("review_status") == "Needs Edit")
        rejected_count = sum(1 for h in normalised_hyps if h.get("review_status") == "Rejected")
        st.caption(f"Approved: {approved_count} · Needs edit: {needs_edit_count} · Rejected: {rejected_count}")
        if validation_issues:
            with st.expander(f"Hypothesis validation issues ({len(validation_issues)})"):
                st.dataframe(validation_issues, use_container_width=True, hide_index=True)

        h1, hcsv, h2 = st.columns(3)
        with h1:
            try:
                phase1_export_xlsx = p1_pipeline.hypotheses_export_xlsx_bytes(normalised_hyps, docs_obj_for_edit, validation_issues)
            except Exception as e:  # noqa: BLE001
                phase1_export_xlsx = None
                st.warning("The Phase 1 XLSX export could not be prepared. You can still approve hypotheses and continue to Phase 2.")
                with st.expander("Show Phase 1 export details", expanded=False):
                    st.code(technical_error_details(e), language="text")
                _log("phase1", "hypothesis_export_xlsx", False, details={"error_type": e.__class__.__name__}, error=friendly_error_message(e))
            if phase1_export_xlsx:
                st.download_button(
                    "Download full Phase 1 export (.xlsx)",
                    phase1_export_xlsx,
                    file_name=f"phase1_hypotheses_{PROJECT_ID[:8]}.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    use_container_width=True,
                    key="p1_download_phase1_export_xlsx_button",
                )
        with hcsv:
            simple_csv = rows_to_simple_hypothesis_csv(normalised_hyps).encode("utf-8-sig")
            st.download_button(
                "Download simple hypotheses CSV",
                simple_csv,
                file_name=f"phase1_hypotheses_simple_{PROJECT_ID[:8]}.csv",
                mime="text/csv",
                use_container_width=True,
                key="p1_download_simple_hypotheses_csv_button",
            )
        with h2:
            if st.button("Approve & send to Phase 2", type="primary", use_container_width=True, key="p1_send_to_phase2_button"):
                approved = p1_pipeline.approved_hypotheses_only(normalised_hyps)
                if not approved:
                    st.warning("No approved hypotheses to send. Mark at least one row as Approved first.")
                    _log("phase1", "hypotheses_approved_for_phase2", False,
                         metrics={"hypothesis_count": 0}, error="No approved hypotheses")
                else:
                    st.session_state["p2_hyps"] = [
                        {
                            "hypothesis_id": h.get("hypothesis_id", ""),
                            "text": h.get("text", ""),
                            "category": h.get("category", ""),
                            "rationale": h.get("rationale", ""),
                            "business_question": h.get("business_question", ""),
                            "audience": h.get("audience", ""),
                            "priority": h.get("priority", "Medium"),
                            "review_status": h.get("review_status", "Approved"),
                            "review_notes": h.get("review_notes", ""),
                            "source_files": h.get("source_files", []),
                            "source_excerpt": h.get("source_excerpt", ""),
                            "validation_warnings": h.get("validation_warnings", []),
                            "duplicate_of": h.get("duplicate_of", ""),
                            "target_segment": "",
                            "study_type": brief.get("study_type", ""),
                        }
                        for h in approved
                    ]
                    p2_study = st.session_state["p2_study"]
                    p2_study["study_type"] = brief.get("study_type", p2_study.get("study_type", ""))
                    p2_study["disease_area"] = brief.get("disease_area", p2_study.get("disease_area", ""))
                    p2_study["target_population"] = brief.get("target_audience") or p2_study.get("target_population", "")
                    p2_study["client_name"] = brief.get("client_name", p2_study.get("client_name", ""))
                    p2_study["target_length"] = brief.get("survey_length", p2_study.get("target_length", ""))
                    if brief.get("goal"):
                        p2_study["user_nuance"] = brief["goal"]
                    # Autofill a study title from the brief if the user hasn't set one.
                    if not p2_study.get("study_title"):
                        _stype, _dis = brief.get("study_type", ""), brief.get("disease_area", "")
                        p2_study["study_title"] = (f"{_stype} — {_dis}".strip(" —")) or "Survey study"
                    st.session_state["p2_study"] = p2_study
                    st.session_state["p2_source"] = "phase1"  # Phase 2 will skip the upload step
                    _set_project_status("Hypotheses Approved")
                    _log("phase1", "hypotheses_approved_for_phase2", True,
                         metrics={"hypothesis_count": len(st.session_state["p2_hyps"])})
                    st.success(f"{len(st.session_state['p2_hyps'])} approved hypotheses sent to Phase 2. "
                               "Opening the Survey outline tab…")
                    st.session_state["_goto_phase2"] = True
                    st.rerun()


# ------------------------------------------------------------ PHASE 2
with tab2:
    st.markdown('<div class="kicker">Phase 2 · AI pipeline</div>', unsafe_allow_html=True)
    st.subheader("Hypotheses + setup → clean survey outline")

    left, right = st.columns([1.1, 1])

    # ---- study setup + hypotheses (editable sample) ----
    with left:
        st.markdown("##### Study setup")
        study = st.session_state["p2_study"]
        study["study_title"] = st.text_input("Study title", study["study_title"], key="p2_study_title_input")
        sc1, sc2 = st.columns(2)
        study["study_type"] = sc1.text_input("Study type", study["study_type"], key="p2_study_type_input")
        study["disease_area"] = sc2.text_input("Disease area", study["disease_area"], key="p2_disease_area_input")
        study["target_population"] = st.text_input("Target population", study["target_population"], key="p2_target_population_input")
        study["target_length"] = st.text_input(
            "Length of survey (incl. screener)", study.get("target_length", ""),
            placeholder="e.g. 30 minutes", key="p2_target_length_input",
            help="Target interview length; the generator sizes the survey to fit.")
        study["client_name"] = st.text_input("Client", study["client_name"], key="p2_client_name_input")
        study["user_nuance"] = st.text_area("Strategic nuance / focus", study["user_nuance"], height=70, key="p2_user_nuance_text_area")
        st.session_state["p2_study"] = study

    with right:
        st.markdown("##### Approved hypotheses")
        _from_p1 = st.session_state.get("p2_source") == "phase1" and bool(st.session_state["p2_hyps"])
        if _from_p1:
            st.success(f"✓ {len(st.session_state['p2_hyps'])} hypotheses received from Phase 1 — no upload needed.")
            _upload_box = st.expander("Replace with a different hypothesis file")
        else:
            st.caption("Already have a hypothesis list? Upload it here to start at Phase 2.")
            _upload_box = st.container()
        with _upload_box:
            hyp_up = st.file_uploader(
                "Hypotheses file (.xlsx / .csv / .txt / .md)",
                type=SUPPORTED_HYPOTHESIS_EXTENSIONS, key="hyp_uploader")
        if hyp_up is not None and _validate_uploads(
            hyp_up, SUPPORTED_HYPOTHESIS_EXTENSIONS, phase="phase2", event="hypothesis_upload_validation"
        ):
            sig = f"{hyp_up.name}:{hyp_up.size}"
            if st.session_state.get("hyp_upload_sig") != sig:
                start = perf_counter()
                try:
                    data = hyp_up.getvalue()
                    write_bytes_unique(PROJECT_PATHS["uploads"], hyp_up.name, data, prefix="phase2_hypotheses")
                    parsed_up = parse_hypotheses_upload(hyp_up)
                    if parsed_up:
                        st.session_state["p2_hyps"] = parsed_up
                        st.session_state["p2_source"] = "upload"
                        st.session_state["hyp_upload_sig"] = sig
                        _log("phase2", "hypothesis_upload_parse", True,
                             metrics={"hypothesis_count": len(parsed_up),
                                      "latency_ms": int((perf_counter() - start) * 1000)})
                        st.success(f"Loaded {len(parsed_up)} hypotheses from {hyp_up.name}")
                        st.rerun()
                    else:
                        st.warning("No hypotheses found in that file. Expecting a 'text' "
                                   "column (xlsx/csv) or one hypothesis per line (txt).")
                        _log("phase2", "hypothesis_upload_parse", False, error="No hypotheses found")
                except Exception as e:  # noqa: BLE001
                    _show_error("Could not parse that hypotheses file", e, "phase2", "hypothesis_upload_parse")

        hyp_text = "\n".join(f"{h['hypothesis_id']}: {h['text']}"
                             for h in st.session_state["p2_hyps"])
        edited = st.text_area("One per line as  ID: text", hyp_text, height=220, key="p2_hypotheses_text_area")
        # parse back while preserving Phase 1 metadata such as category/construct,
        # rationale, source files, and review notes where the hypothesis ID still matches.
        existing_by_id = {str(h.get("hypothesis_id", "")).strip(): dict(h) for h in st.session_state["p2_hyps"]}
        parsed = []
        for ln in edited.splitlines():
            ln = ln.strip()
            if not ln:
                continue
            if ":" in ln:
                hid, txt = ln.split(":", 1)
                hid = hid.strip()
                base = existing_by_id.get(hid, {})
                row = dict(base)
                row.update({
                    "hypothesis_id": hid,
                    "text": txt.strip(),
                    "target_segment": base.get("target_segment", "All") or "All",
                    "study_type": study["study_type"],
                })
                parsed.append(row)
        if parsed:
            st.session_state["p2_hyps"] = parsed
        st.caption(f"{len(st.session_state['p2_hyps'])} hypotheses")

    st.divider()

    # ---- Step 1: propose sections ----
    run1 = st.button("Propose sections", type="primary",
                     disabled=not bool(st.session_state["api_key"]),
                     key="p2_propose_sections_button")
    if not st.session_state["api_key"]:
        st.caption("Set an API key in the configuration panel above to run the AI pipeline.")

    if run1:
        client = get_client()
        start = perf_counter()
        try:
            study_obj = StudySetup.model_validate(st.session_state["p2_study"])
            hyps_obj = [Hypothesis.model_validate(h) for h in st.session_state["p2_hyps"]]
            with st.status("Proposing sections…", expanded=True) as status:
                def prog(step, st_, detail):
                    status.write(f"**{step}** — {detail}")
                secs = pipeline.run_section_suggester(client, study_obj, hyps_obj, prog)
                st.session_state["p2_sections"] = [s.model_dump() for s in secs]
                st.session_state["p2_result"] = None
                st.session_state["p2_editor_rows"] = None
                st.session_state["p2_spec_path"] = None
                st.session_state["p2_final_approved"] = False
                status.update(label=f"{len(secs)} sections proposed", state="complete")
                _log("phase2", "section_suggestion", True,
                     metrics={"section_count": len(secs),
                              "hypothesis_count": len(hyps_obj),
                              "latency_ms": int((perf_counter() - start) * 1000)})
        except Exception as e:  # noqa: BLE001
            _show_error("Section suggestion failed", e, "phase2", "section_suggestion")

    # ---- Human gate: review/edit/lock sections ----
    if st.session_state["p2_sections"]:
        st.markdown("##### Human gate — review & lock sections")
        st.caption("Edit section IDs/titles, remove rows, then lock to run the rest of the pipeline.")
        secs = st.session_state["p2_sections"]
        edited_rows = st.data_editor(
            secs, num_rows="dynamic", use_container_width=True,
            column_config={
                "section_id": st.column_config.TextColumn("Section ID", width="small"),
                "section_title": st.column_config.TextColumn("Section title", width="large"),
                "rationale": st.column_config.TextColumn("Rationale"),
                "is_must_have": st.column_config.CheckboxColumn("Must-have"),
            }, key=f"section_editor_{st.session_state.get('p2_sections_ver', 0)}")
        if hasattr(edited_rows, "to_dict"):
            st.session_state["p2_sections"] = edited_rows.to_dict("records")
        else:
            st.session_state["p2_sections"] = edited_rows

        # --- CSV round-trip: download the sections, edit offline, upload before locking ---
        def _sections_to_csv(rows) -> bytes:
            buf = io.StringIO()
            w = csv.writer(buf)
            w.writerow(["Section_ID", "Section_Title", "Rationale", "Must_Have"])
            for r in rows:
                w.writerow([r.get("section_id", ""), r.get("section_title", ""),
                            r.get("rationale", ""), "yes" if r.get("is_must_have") else "no"])
            return buf.getvalue().encode("utf-8")

        csv_dl, csv_up = st.columns(2)
        with csv_dl:
            st.download_button(
                "Download sections (.csv)", _sections_to_csv(st.session_state["p2_sections"]),
                file_name=f"survey_sections_{PROJECT_ID[:8]}.csv", mime="text/csv",
                use_container_width=True, key="p2_sections_download_csv")
        with csv_up:
            sec_csv = st.file_uploader(
                "Upload edited sections (.csv)", type=["csv"], key="p2_sections_upload_csv",
                help="Replaces the table above. Columns: Section_ID, Section_Title, Rationale, Must_Have.")
        if sec_csv is not None:
            file_id = f"{sec_csv.name}:{getattr(sec_csv, 'size', '')}"
            if st.session_state.get("p2_sections_csv_id") != file_id:
                try:
                    text = sec_csv.getvalue().decode("utf-8-sig")
                    new_rows = []
                    for row in csv.DictReader(io.StringIO(text)):
                        low = {(k or "").strip().lower(): (v or "").strip() for k, v in row.items()}
                        sid = low.get("section_id") or low.get("section id") or low.get("section") or ""
                        title = low.get("section_title") or low.get("section title") or ""
                        rationale = low.get("rationale") or ""
                        must = (low.get("must_have") or low.get("must-have") or low.get("must have") or "").lower() in ("y", "yes", "true", "1")
                        if sid or title:
                            new_rows.append({"section_id": sid, "section_title": title,
                                             "rationale": rationale, "is_must_have": must})
                    if not new_rows:
                        st.error("No sections found. Expected columns: Section_ID, Section_Title, Rationale, Must_Have.")
                    else:
                        st.session_state["p2_sections"] = new_rows
                        st.session_state["p2_sections_csv_id"] = file_id
                        st.session_state["p2_sections_ver"] = st.session_state.get("p2_sections_ver", 0) + 1
                        _log("phase2", "sections_csv_import", True, metrics={"section_count": len(new_rows)})
                        st.success(f"Loaded {len(new_rows)} section(s) from CSV — review above, then lock.")
                        st.rerun()
                except Exception as e:  # noqa: BLE001
                    _show_error("Could not read the sections CSV", e, "phase2", "sections_csv_import")

        run2 = st.button("Lock sections & run pipeline (steps 3–11)", type="primary", key="p2_lock_sections_run_pipeline_button")
        if run2:
            client = get_client()
            start = perf_counter()
            try:
                study_obj = StudySetup.model_validate(st.session_state["p2_study"])
                hyps_obj = [Hypothesis.model_validate(h) for h in st.session_state["p2_hyps"]]
                secs_obj = [SectionProposal.model_validate(s) for s in st.session_state["p2_sections"]]
                _set_project_status("Sections Approved")
                _write_intermediate_json("phase2_approved_sections", [s.model_dump() for s in secs_obj])
                with st.status("Running pipeline… (~3–6 min: drafts → parallel review → revises once)", expanded=True) as status:
                    def prog(step, st_, detail):
                        icon = {"start": "▶", "done": "✓", "skip": "—", "info": "·"}.get(st_, "·")
                        status.write(f"{icon} **{step}** {('— ' + detail) if detail else ''}")
                    result = pipeline.run_pipeline_after_gate(
                        client, study_obj, hyps_obj, secs_obj, prog)
                    p2_payload = {
                        "questions": [q.model_dump() for q in result["questions"]],
                        "mappings": [m.model_dump(by_alias=True) for m in result["mappings"]],
                        "validation": result["validation"].model_dump(),
                        "consolidated": result["consolidated"].model_dump(),
                        "rounds_run": result["rounds_run"],
                        "revision_diffs": result.get("revision_diffs", []),
                        "question_id_map": result.get("question_id_map", {}),
                        "artifacts": result.get("artifacts", {}),
                        "llm_usage": result.get("llm_usage", {}),
                    }
                    st.session_state["p2_result"] = p2_payload
                    st.session_state["p2_editor_rows"] = questions_to_editor_rows(result["questions"])
                    st.session_state["p2_spec_path"] = None
                    st.session_state["p2_export_override"] = False
                    st.session_state["p2_final_approved"] = False
                    st.session_state["p2_usage"] = p2_payload["llm_usage"]
                    _set_project_status("Needs Review" if result["validation"].blockers else "Questions Generated")
                    artifact_path = _write_intermediate_json("phase2_pipeline_result", p2_payload)
                    st.session_state["p2_artifact_path"] = artifact_path
                    status.update(
                        label=f"Pipeline complete — {len(result['questions'])} questions, "
                              f"{result['rounds_run']} review round(s)", state="complete")
                    usage = p2_payload.get("llm_usage") or {}
                    _log("phase2", "pipeline", True,
                         metrics={"question_count": len(result["questions"]),
                                  "section_count": len(secs_obj),
                                  "hypothesis_count": len(hyps_obj),
                                  "validation_status": result["validation"].status,
                                  "rounds_run": result["rounds_run"],
                                  "latency_ms": int((perf_counter() - start) * 1000),
                                  "llm_calls": usage.get("calls"),
                                  "estimated_total_tokens": usage.get("estimated_total_tokens")})
            except Exception as e:  # noqa: BLE001
                _show_error("Pipeline failed", e, "phase2", "pipeline")

    # ---- results / human review / gated export ----
    if st.session_state["p2_result"]:
        res = st.session_state["p2_result"]
        st.markdown("##### Results")
        try:
            study_obj = StudySetup.model_validate(st.session_state["p2_study"])
            hyps_obj = [Hypothesis.model_validate(h) for h in st.session_state["p2_hyps"]]
            secs_obj = [SectionProposal.model_validate(s) for s in (st.session_state.get("p2_sections") or [])]
            mappings_obj = [HMapping.model_validate(m) for m in res.get("mappings", [])]
            consolidated = ConsolidatedResult.model_validate(res.get("consolidated", {}))
            base_questions = [SurveyQuestion.model_validate(q) for q in res.get("questions", [])]

            if st.session_state.get("p2_editor_rows") is None:
                st.session_state["p2_editor_rows"] = questions_to_editor_rows(base_questions)

            st.markdown("##### Final question editor")
            st.caption("Edit questions, options, scale anchors, routing, termination logic, notes, and source hypothesis coverage before export. Multi-line cells accept one item per line.")
            edited_rows = st.data_editor(
                st.session_state["p2_editor_rows"],
                num_rows="dynamic",
                use_container_width=True,
                key=f"p2_question_editor_{st.session_state.get('p2_editor_ver', 0)}",
                column_config={
                    "section_id": st.column_config.TextColumn("Section ID", width="small"),
                    "section_title": st.column_config.TextColumn("Section", width="medium"),
                    "q_id": st.column_config.TextColumn("Question ID", width="small"),
                    "question_type": st.column_config.SelectboxColumn("Question type", options=SUPPORTED_QUESTION_TYPES, width="medium"),
                    "question_text": st.column_config.TextColumn("Question text", width="large"),
                    "options": st.column_config.TextColumn("Options / rows", width="large"),
                    "grid_columns": st.column_config.TextColumn("Grid columns", width="medium"),
                    "dropdown_options": st.column_config.TextColumn("Dropdown options", width="medium"),
                    "scale_min": st.column_config.NumberColumn("Scale min", width="small"),
                    "scale_max": st.column_config.NumberColumn("Scale max", width="small"),
                    "scale_anchor_low": st.column_config.TextColumn("Low anchor", width="medium"),
                    "scale_anchor_high": st.column_config.TextColumn("High anchor", width="medium"),
                    "routing_condition": st.column_config.TextColumn("Routing condition", width="medium"),
                    "routing_next": st.column_config.TextColumn("Routing next", width="small"),
                    "termination_logic": st.column_config.TextColumn("Termination logic", width="medium"),
                    "option_level_logic": st.column_config.TextColumn("Option logic", width="medium"),
                    "programming_instructions": st.column_config.TextColumn("Programming notes", width="medium"),
                    "source_hypotheses": st.column_config.TextColumn("Source hypotheses", width="medium"),
                    "interviewer_notes": st.column_config.TextColumn("Interviewer notes", width="medium"),
                    "flag_status": st.column_config.SelectboxColumn("Flag", options=["clean", "flagged", "delete"], width="small"),
                },
            )
            edited_rows_records = edited_rows.to_dict("records") if hasattr(edited_rows, "to_dict") else edited_rows
            st.session_state["p2_editor_rows"] = edited_rows_records

            # --- CSV round-trip: download the questions, edit offline, upload before export ---
            _QEDITOR_COLS = ["section_id", "section_title", "q_id", "question_type", "question_text",
                             "options", "grid_columns", "dropdown_options", "scale_min", "scale_max",
                             "scale_anchor_low", "scale_anchor_high", "routing_condition", "routing_next",
                             "termination_logic", "option_level_logic", "programming_instructions",
                             "source_hypotheses", "interviewer_notes", "flag_status"]

            def _questions_to_csv(rows) -> bytes:
                buf = io.StringIO(); w = csv.writer(buf); w.writerow(_QEDITOR_COLS)
                for r in rows:
                    w.writerow(["" if r.get(c) is None else r.get(c) for c in _QEDITOR_COLS])
                return buf.getvalue().encode("utf-8")

            qcsv_dl, qcsv_up = st.columns(2)
            with qcsv_dl:
                st.download_button(
                    "Download questions (.csv)", _questions_to_csv(st.session_state["p2_editor_rows"]),
                    file_name=f"survey_questions_{PROJECT_ID[:8]}.csv", mime="text/csv",
                    use_container_width=True, key="p2_questions_download_csv")
            with qcsv_up:
                q_csv = st.file_uploader(
                    "Upload edited questions (.csv)", type=["csv"], key="p2_questions_upload_csv",
                    help="Replaces the table above. Keep the downloaded column headers; multi-line cells use one item per line.")
            if q_csv is not None:
                fid = f"{q_csv.name}:{getattr(q_csv, 'size', '')}"
                if st.session_state.get("p2_qcsv_id") != fid:
                    try:
                        text = q_csv.getvalue().decode("utf-8-sig")
                        new_rows = []
                        for row in csv.DictReader(io.StringIO(text)):
                            low = {(k or "").strip().lower(): ("" if v is None else v) for k, v in row.items()}
                            rec = {c: low.get(c, "") for c in _QEDITOR_COLS}
                            for sk in ("scale_min", "scale_max"):
                                sv = str(rec[sk]).strip()
                                try:
                                    rec[sk] = int(float(sv)) if sv and sv.lower() not in ("nan", "none") else None
                                except ValueError:
                                    rec[sk] = None
                            if not str(rec.get("flag_status", "")).strip():
                                rec["flag_status"] = "clean"
                            if str(rec.get("q_id", "")).strip() or str(rec.get("question_text", "")).strip():
                                new_rows.append(rec)
                        if not new_rows:
                            st.error("No questions found in the CSV. Keep the downloaded column headers.")
                        else:
                            st.session_state["p2_editor_rows"] = new_rows
                            st.session_state["p2_qcsv_id"] = fid
                            st.session_state["p2_editor_ver"] = st.session_state.get("p2_editor_ver", 0) + 1
                            _log("phase2", "questions_csv_import", True, metrics={"question_count": len(new_rows)})
                            st.success(f"Loaded {len(new_rows)} question(s) from CSV — review above, then export.")
                            st.rerun()
                    except Exception as e:  # noqa: BLE001
                        _show_error("Could not read the questions CSV", e, "phase2", "questions_csv_import")

            edited_questions = editor_rows_to_questions(st.session_state["p2_editor_rows"])
            edited_validation = validate_phase2(
                edited_questions,
                mappings_obj,
                [h.hypothesis_id for h in hyps_obj],
                secs_obj,
            )
            res["questions"] = [q.model_dump() for q in edited_questions]
            res["validation"] = edited_validation.model_dump()
            st.session_state["p2_result"] = res

            m1, m2, m3, m4, m5 = st.columns(5)
            covered = len(edited_validation.coverage_map) - len(edited_validation.uncovered_hypotheses)
            total = len(edited_validation.coverage_map)
            m1.metric("Questions", len(edited_questions))
            m2.metric("Hypotheses covered", f"{covered}/{total}")
            m3.metric("Review rounds", res.get("rounds_run", 0))
            m4.metric("Validation", edited_validation.status)
            m5.metric("Est. LOI", f"{edited_validation.estimated_length_minutes:.1f} min")

            usage = res.get("llm_usage") or res.get("usage") or st.session_state.get("p2_usage") or {}
            if usage:
                with st.expander("Model usage / cost estimate"):
                    st.dataframe([usage], use_container_width=True, hide_index=True)

            if edited_validation.blockers:
                st.error(f"{len(edited_validation.blockers)} validation blocker(s) must be fixed or explicitly overridden before draft export.")
                with st.expander("Validation blockers", expanded=True):
                    for b in edited_validation.blockers:
                        st.write("• " + str(b))
            if edited_validation.warnings:
                with st.expander(f"{len(edited_validation.warnings)} validator warnings / burden notes"):
                    for w in edited_validation.warnings:
                        st.write("• " + str(w))

            with st.expander("Coverage matrix", expanded=bool(edited_validation.uncovered_hypotheses)):
                st.dataframe(coverage_matrix_rows(hyps_obj, edited_validation), use_container_width=True, hide_index=True)

            issue_rows = issue_dashboard_rows(edited_validation, consolidated)
            with st.expander(f"Issue dashboard ({len(issue_rows)} issues)", expanded=bool(edited_validation.blockers)):
                if issue_rows:
                    st.dataframe(issue_rows, use_container_width=True, hide_index=True)
                else:
                    st.caption("No validation or review issues recorded.")

            if res.get("revision_diffs"):
                with st.expander(f"Revision diff view ({len(res['revision_diffs'])} changes)"):
                    st.dataframe(res["revision_diffs"], use_container_width=True, hide_index=True)

            if st.session_state.get("p2_artifact_path") and os.path.exists(st.session_state["p2_artifact_path"]):
                with open(st.session_state["p2_artifact_path"], "rb") as f:
                    st.download_button(
                        "Download Phase 2 intermediate artifacts (.json)", f,
                        file_name=os.path.basename(st.session_state["p2_artifact_path"]),
                        mime="application/json",
                        use_container_width=True,
                        key="p2_download_intermediate_artifacts_button",
                    )

            st.markdown("##### Export gate")
            if edited_validation.blockers:
                override = st.checkbox(
                    "Export draft despite blockers",
                    key="p2_export_override",
                    help="Use only when you intentionally want a draft workbook with known blockers.",
                )
            else:
                st.session_state["p2_export_override"] = False
                override = False
            approved = st.checkbox(
                "I have reviewed and approve this survey outline for export",
                key="p2_final_approved",
            )
            policy = validation_policy(edited_validation, override=override, approved=approved)
            if policy["requires_approval"]:
                st.info("Human approval is required before workbook export is enabled.")
            if policy["requires_override"] and not policy["override_used"]:
                st.warning("Blockers require either fixes or an explicit draft override before export.")

            if st.button("Create / refresh survey outline workbook", type="primary", disabled=not policy["can_export"], use_container_width=True, key="p2_create_workbook_button"):
                spec_version = PROJECT_STORE.next_artifact_version(PROJECT_ID, "survey_outline_workbook")
                spec_path = unique_path(PROJECT_PATHS["outputs"], f"survey_outline_{safe_filename(study_obj.study_title, 'study')}_v{spec_version:03d}_{PROJECT_ID[:8]}.xlsx")
                export_mode = "Draft" if policy["override_used"] else "Final"
                write_survey_spec(
                    str(spec_path), study_obj, edited_questions, hyps_obj, secs_obj,
                    coverage_map=edited_validation.coverage_map,
                    validation_report=edited_validation,
                    final_approved=approved,
                    blocker_override=policy["override_used"],
                    export_mode=export_mode,
                )
                st.session_state["p2_spec_path"] = str(spec_path)
                _set_project_status("Exported")
                PROJECT_STORE.record_artifact(PROJECT_ID, "survey_outline_workbook", str(spec_path), version=spec_version, metadata={"export_mode": export_mode, "validation_status": edited_validation.status})
                _write_intermediate_json("phase2_final_edited_questions", [q.model_dump() for q in edited_questions])
                _write_intermediate_json("phase2_final_validation", edited_validation.model_dump())
                _log("phase2", "workbook_export", True,
                     metrics={"question_count": len(edited_questions),
                              "validation_status": edited_validation.status,
                              "blocker_override": bool(policy["override_used"]),
                              "approved": bool(approved)})
                st.success(f"Survey outline workbook created: {os.path.basename(str(spec_path))}")

            if st.session_state["p2_spec_path"]:
                st.markdown("##### Outputs")
                d1, d2 = st.columns(2)
                with d1:
                    with open(st.session_state["p2_spec_path"], "rb") as f:
                        st.download_button(
                            "Download survey outline (.xlsx)", f,
                            file_name=os.path.basename(st.session_state["p2_spec_path"]), use_container_width=True,
                            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                            key="p2_download_survey_outline_button")
                with d2:
                    st.caption("**Phase 3 (Word document):** take this workbook to the "
                               "**Questionnaire document** tab to render the formatted `.docx`.")
        except Exception as e:  # noqa: BLE001
            _show_error("Could not display Phase 2 results", e, "phase2", "result_display")


# ------------------------------------------------------------ PHASE 3 (Python / python-docx)
with tab3:
    st.markdown('<div class="kicker">Phase 3 · Questionnaire document</div>', unsafe_allow_html=True)
    st.subheader("Render the formatted Word questionnaire (.docx)")
    st.caption("Produces the formatted Word questionnaire from the survey-outline workbook — "
               "same layout as the Stage-3 renderer (purple tables, programming tags, page "
               "breaks), with internal validation/QA metadata omitted. Uses the workbook you "
               "exported in Phase 2, or upload one below.")

    src_path = st.session_state.get("p2_spec_path")
    up = st.file_uploader("Survey-outline workbook (.xlsx)", type=["xlsx"], key="p3_wb_upload",
                          help="Optional — defaults to the workbook you exported in Phase 2.")
    if up is not None and _validate_uploads(up, SUPPORTED_WORKBOOK_EXTENSIONS, phase="phase3", event="workbook_upload"):
        wb_path = write_bytes_unique(PROJECT_PATHS["uploads"], safe_filename(up.name, "workbook.xlsx"), up.getvalue())
        src_path = str(wb_path)
    elif src_path:
        st.caption(f"Using the workbook exported in Phase 2: **{os.path.basename(src_path)}**")

    if not src_path:
        st.info("Export a survey-outline workbook in the **Survey outline** tab first, or upload one above.")
    else:
        if st.button("Generate Word questionnaire (.docx)", type="primary",
                     use_container_width=True, key="p3_render_button"):
            try:
                out_path = unique_path(PROJECT_PATHS["outputs"], f"questionnaire_{PROJECT_ID[:8]}.docx")
                render_questionnaire_docx(src_path, str(out_path))
                st.session_state["p3_docx_path"] = str(out_path)
                _record_artifact("questionnaire_docx", str(out_path))
                _log("phase3", "docx_render", True, details={"source": os.path.basename(src_path)})
                st.success(f"Word questionnaire created: {os.path.basename(str(out_path))}")
            except Exception as e:  # noqa: BLE001
                _show_error("Could not render the Word questionnaire", e, "phase3", "docx_render")

        if st.session_state.get("p3_docx_path"):
            with open(st.session_state["p3_docx_path"], "rb") as f:
                st.download_button(
                    "Download questionnaire (.docx)", f,
                    file_name=os.path.basename(st.session_state["p3_docx_path"]),
                    mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    use_container_width=True, key="p3_download_docx_button")

