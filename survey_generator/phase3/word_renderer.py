"""Render the Survey-Outline workbook into a Word questionnaire — a faithful Python
(python-docx) port of the Stage-3 R renderer (stage3_core.R, officer/flextable).

Mirrors the R layout: orange cover, programming-instructions legend, sample plan,
screening criteria, outline summary, disclosures, intro; then per-section page
breaks and per-question blocks (purple-header tables, grey programming tags,
[PAGE BREAK]). The ONLY intentional change vs the R renderer: the internal
`[VALIDATION: ...]` tag is never printed.
"""
from __future__ import annotations

import re
from datetime import date
from typing import Dict, List

import openpyxl
from docx import Document
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

# ---- style constants (from stage3_core.R) -----------------------------------
FONT = "Arial"
DOC_W_CM = 16.5

Q_ID_SZ = 11; Q_TXT_SZ = 11; RESP_INSTR_SZ = 10; NOTE_SZ = 10
SECTION_TITLE_SZ = 14; FRONT_TITLE_SZ = 16; COVER_TITLE_SZ = 22
COVER_META_SZ = 11; COVER_CLIENT_SZ = 12; SMALL_SZ = 10
TBL_BODY_SZ = 10; TBL_COMPACT_SZ = 8.5; TBL_COLID_SZ = 8
PROG_TAG_SZ = 9; QTYPE_TAG_SZ = 8.5

TABLE_HEADER_BG = "5F2987"; TABLE_BORDER = "000000"
PROG_TAG_BG = "D9D9D9"; PROG_TAG_TXT = "00629B"
QTYPE_TAG_BG = "EEE7F8"; QTYPE_TAG_TXT = "5F2987"
DARK = "292B2D"; NOTE_GREY = "808080"; RESP_INSTR_TXT = "6E6E73"; ORANGE = "ED8B00"; WARNING = "A64B00"

NUMERIC_PH = "XX"; PERCENT_PH = "XX%"; OPENEND_PH = "____"; CHECKBOX = "☐"

RATING_TYPES = {"RATING_7", "RATING_7_IMPORTANCE", "RATING_7_SATISFACTION",
                "RATING_7_LIKELIHOOD", "RATING_7_FAMILIARITY"}
SUPPORTED = RATING_TYPES | {"RATING_DUAL", "SINGLE_SELECT", "MULTI_SELECT", "NUMERIC",
                            "PERCENT_ALLOCATION", "RANKING", "AWARENESS_USAGE", "DROPDOWN",
                            "OPEN_END", "GRID", "TPP_DISPLAY", "INSTRUCTION"}

# Study/PMR rationale & internal change-notes that must never reach the questionnaire —
# the programmer wants directives, not the "why". (Genuine directives — RANDOMIZE, SHOW IF,
# PIPE, TERMINATE, ANCHOR, RECORD, FORCE — don't match these patterns and are kept.)
_META_RX = re.compile(
    r"\bGEN\d+\b|\bfixes\b|see inserted question|split into\b.*\bband|"
    r"note to programmer|has been (removed|split|added|replaced|moved|dropped|renamed)|"
    r"generic descriptor|\bafter\b.{0,60}\bestablished\b|\basked in section\b",
    re.I,
)
# A line that expresses a termination / screen-out.
_TERM_RX = re.compile(r"\bterminate\b|\bscreen[\s_]?out\b", re.I)


def _rgb(h): return RGBColor.from_string(h)


# ---- parsing (ports parse_code_label_lines / extract_inline_logic) ----------
def _split_lines(cell: str) -> List[str]:
    s = (cell or "").replace("\r\n", "\n").replace("\r", "\n")
    return [ln.strip() for ln in s.split("\n") if ln.strip()]


def _strip_wrapper(s: str) -> str:
    s = s.strip()
    if s.startswith("[") and s.endswith("]"):
        s = s[1:-1].strip()
    return s


def _extract_logic(label: str):
    notes = re.findall(r"\{([^{}]+)\}", label)
    clean = re.sub(r"\s*\{[^{}]+\}", "", label).strip()
    return clean, "\n".join(n.strip() for n in notes if n.strip())


def _parse_items(cell: str):
    """-> list of dicts {code,label,logic}."""
    out = []
    for i, raw in enumerate(_split_lines(cell), start=1):
        ln = _strip_wrapper(raw)
        if "|" in ln:
            code, label = ln.split("|", 1); code, label = code.strip(), label.strip()
        elif "=" in ln:
            code, label = ln.split("=", 1); code, label = code.strip(), label.strip()
        else:
            code, label = str(i), ln
        label, logic = _extract_logic(label)
        out.append({"code": code, "label": label, "logic": logic})
    return out


def _parse_dual(cell: str):
    pairs = []
    for it in _parse_items(cell):
        lab = it["label"]
        m = re.split(r"\s*(?:\[vs\.?\]|vs\.?)\s*", lab, flags=re.I)
        left = m[0].strip() if m else lab
        right = m[1].strip() if len(m) > 1 else "[RIGHT STATEMENT TBU]"
        pairs.append({"code": it["code"], "left": left, "right": right, "logic": it["logic"]})
    return pairs


# ---- workbook reading -------------------------------------------------------
def _read_workbook(path: str):
    wb = openpyxl.load_workbook(path, data_only=True)
    meta: Dict[str, str] = {}
    sample_plan: List[Dict[str, str]] = []
    if "Survey_Metadata" in wb.sheetnames:
        rows = list(wb["Survey_Metadata"].iter_rows(values_only=True))
        mode = "meta"
        for r in rows:
            if not r:
                continue
            c0 = str(r[0]).strip() if r[0] is not None else ""
            c1 = str(r[1]).strip() if len(r) > 1 and r[1] is not None else ""
            c2 = str(r[2]).strip() if len(r) > 2 and r[2] is not None else ""
            c3 = str(r[3]).strip() if len(r) > 3 and r[3] is not None else ""
            if c0.lower() == "segment_id":
                mode = "sample"; continue
            if c0.lower() == "field" and c1.lower() == "value":
                continue
            if mode == "meta" and c0:
                meta[c0] = c1
            elif mode == "sample" and (c0 or c1):
                sample_plan.append({"Segment_ID": c0, "Audience_Segment": c1, "Target_N": c2, "Qualification_Notes": c3})
    questions = []
    ws = wb["Survey_Outline"]
    data = list(ws.iter_rows(values_only=True))
    if data:
        hdr = [str(c).strip() if c is not None else "" for c in data[0]]
        for r in data[1:]:
            d = {hdr[i]: ("" if v is None else str(v)) for i, v in enumerate(r) if i < len(hdr)}
            if not (d.get("Question_ID") or d.get("Question_Text")):
                continue
            questions.append({
                "section_id": d.get("Section_ID", ""), "section_title": d.get("Section_Title", ""),
                "section_objective": d.get("Section_Objective", ""),
                "question_id": d.get("Question_ID", ""), "question_text": re.sub(r"\*\*", "", d.get("Question_Text", "")),
                "question_type": (d.get("Question_Type", "") or "").strip().upper(),
                "items": _parse_items(d.get("Statements_or_Options", "")),
                "grid_cols": _parse_items(d.get("Grid_Columns", "")),
                "dropdown_options": _parse_items(d.get("Dropdown_Options", "")),
                "dual_pairs": _parse_dual(d.get("Statements_or_Options", "")),
                "programming": _split_lines(d.get("Programming_Instructions", "")),
                "skip_logic": _split_lines(d.get("Question_Skip_Logic", "")),
                "option_logic": _split_lines(d.get("Option_Level_Logic", "")),
                "termination_logic": _split_lines(d.get("Termination_Logic", "")),
                # validation parsed but DELIBERATELY NOT rendered
                "notes": (d.get("Notes", "") or "").strip(),
            })
    return meta, sample_plan, questions


# ---- low-level docx helpers -------------------------------------------------
def _shade(el, fill):
    pr = el.get_or_add_rPr() if el.tag.endswith("}r") else el
    shd = OxmlElement("w:shd"); shd.set(qn("w:val"), "clear"); shd.set(qn("w:fill"), fill)
    pr.append(shd)


def _run(p, text, *, size, bold=False, italic=False, color=DARK, shading=None):
    r = p.add_run(str(text))
    r.font.name = FONT; r.font.size = Pt(size); r.font.bold = bold; r.font.italic = italic
    r.font.color.rgb = _rgb(color)
    if shading:
        rpr = r._r.get_or_add_rPr()
        shd = OxmlElement("w:shd"); shd.set(qn("w:val"), "clear"); shd.set(qn("w:fill"), shading)
        rpr.append(shd)
    return r


def _para(doc, *runs, line_spacing=None, space_after=4, align=WD_ALIGN_PARAGRAPH.LEFT,
          outline_level=None):
    p = doc.add_paragraph(); p.alignment = align
    p.paragraph_format.space_after = Pt(space_after)
    if line_spacing:
        p.paragraph_format.line_spacing = line_spacing
    if outline_level is not None:
        # Word builds the Navigation pane from outline levels; set it directly so the
        # heading shows up there WITHOUT changing the paragraph's visual formatting.
        pPr = p._p.get_or_add_pPr()
        lvl = OxmlElement("w:outlineLvl"); lvl.set(qn("w:val"), str(outline_level))
        pPr.append(lvl)
    for spec in runs:
        _run(p, spec[0], **spec[1])
    return p


def _R(text, **kw):  # run spec helper
    return (text, kw)


def _tag_line(doc, text):
    _para(doc, _R(f"[{text}]", size=PROG_TAG_SZ, bold=True, color=PROG_TAG_TXT, shading=PROG_TAG_BG))


def _qtype_tag(doc, qtype):
    _para(doc, _R(f"[{qtype.replace('_', ' ')} QUESTION]", size=QTYPE_TAG_SZ, bold=True,
                  color=QTYPE_TAG_TXT, shading=QTYPE_TAG_BG))


def _resp_instr(doc, text):
    _para(doc, _R(text, size=RESP_INSTR_SZ, italic=True, color=RESP_INSTR_TXT), line_spacing=1.25)


# ---- table helpers ----------------------------------------------------------
def _set_borders(tbl):
    tblPr = tbl._tbl.tblPr
    borders = OxmlElement("w:tblBorders")
    for edge in ("top", "left", "bottom", "right", "insideH", "insideV"):
        e = OxmlElement(f"w:{edge}")
        e.set(qn("w:val"), "single"); e.set(qn("w:sz"), "4")
        e.set(qn("w:space"), "0"); e.set(qn("w:color"), TABLE_BORDER)
        borders.append(e)
    tblPr.append(borders)
    layout = OxmlElement("w:tblLayout"); layout.set(qn("w:type"), "fixed"); tblPr.append(layout)


def _widths(tbl, cm_list):
    total = sum(cm_list) or 1
    scaled = [c * (DOC_W_CM / total) for c in cm_list]
    tbl.autofit = False
    for row in tbl.rows:
        for i, w in enumerate(scaled):
            if i < len(row.cells):
                row.cells[i].width = Cm(w)


def _cell(cell, text, *, size, bold=False, color=DARK, fill=None, align=WD_ALIGN_PARAGRAPH.LEFT,
          tag_lines=None):
    cell.text = ""
    p = cell.paragraphs[0]; p.alignment = align
    p.paragraph_format.space_after = Pt(0)
    _run(p, text, size=size, bold=bold, color=color)
    if tag_lines:
        for t in tag_lines:
            tp = cell.add_paragraph(); tp.alignment = align; tp.paragraph_format.space_after = Pt(0)
            _run(tp, t, size=TBL_COLID_SZ, bold=True, color=PROG_TAG_TXT, shading=PROG_TAG_BG)
    if fill:
        _shade(cell._tc.get_or_add_tcPr(), fill)


def _hdr_cell(cell, text, *, size=TBL_BODY_SZ, align=WD_ALIGN_PARAGRAPH.CENTER, tag=None):
    _cell(cell, text, size=size, bold=True, color="FFFFFF", fill=TABLE_HEADER_BG, align=align,
          tag_lines=[tag] if tag else None)
    # white tag text on header: recolor the tag run white-on-grey stays readable; keep blue tag
    if tag:
        for p in cell.paragraphs[1:]:
            for r in p.runs:
                r.font.color.rgb = _rgb(PROG_TAG_TXT)


def _label_with_logic(it):
    lab = it["label"]
    if it.get("logic"):
        tags = "  ".join(f"[{ln}]" for ln in it["logic"].split("\n") if ln.strip())
        return f"{lab}\n{tags}"
    return lab


def _basic_table(doc, headers, rows, cm_list, *, body_size=TBL_BODY_SZ, aligns=None, col_tags=None):
    n = len(headers)
    tbl = doc.add_table(rows=1, cols=n); tbl.alignment = WD_TABLE_ALIGNMENT.LEFT
    for j, h in enumerate(headers):
        _hdr_cell(tbl.rows[0].cells[j], h, tag=(col_tags or {}).get(j))
    for row in rows:
        cells = tbl.add_row().cells
        for j, val in enumerate(row):
            a = (aligns or {}).get(j, WD_ALIGN_PARAGRAPH.LEFT)
            _cell(cells[j], val, size=body_size, align=a)
    _set_borders(tbl); _widths(tbl, cm_list)
    return tbl


# ---- per-type table builders (port of build_* ) -----------------------------
def _items_or_ph(items, ph="[ITEM TBU]"):
    return items if items else [{"code": "1", "label": ph, "logic": ""}]


def _option_table(doc, items, symbol, resp_header):
    rows = [[it["code"], _label_with_logic(it), symbol] for it in items]
    _basic_table(doc, ["Code", "Option", resp_header], rows, [1.3, 12.8, 2.9],
                 aligns={0: WD_ALIGN_PARAGRAPH.CENTER, 2: WD_ALIGN_PARAGRAPH.CENTER},
                 col_tags={2: "[A]"})


def _entry_table(doc, items, ph, header):
    rows = [[it["code"], _label_with_logic(it), ph] for it in items]
    _basic_table(doc, ["Code", "Label", header], rows, [1.3, 12.8, 2.9],
                 aligns={0: WD_ALIGN_PARAGRAPH.CENTER, 2: WD_ALIGN_PARAGRAPH.CENTER}, col_tags={2: "[A]"})


def _percent_table(doc, items):
    items = list(items)
    if not any(it["label"].lower() == "total" or it["code"].upper() == "TOTAL" for it in items):
        items = items + [{"code": "TOTAL", "label": "Total", "logic": ""}]
    rows = []
    for it in items:
        is_total = it["label"].lower() == "total" or it["code"].upper() == "TOTAL"
        rows.append([it["code"], _label_with_logic(it), "[AUTOSUM, MUST SUM TO 100%]" if is_total else PERCENT_PH])
    _basic_table(doc, ["Code", "Label", "Percent"], rows, [1.3, 12.4, 3.3],
                 aligns={0: WD_ALIGN_PARAGRAPH.CENTER, 2: WD_ALIGN_PARAGRAPH.CENTER}, col_tags={2: "[A]"})


def _awareness_table(doc, items):
    cols = ["Never heard of it", "Aware, never used", "Used for SOME", "Used for MANY"]
    rows = [[it["code"], _label_with_logic(it), "O", "O", "O", "O"] for it in items]
    aligns = {0: WD_ALIGN_PARAGRAPH.CENTER, 2: WD_ALIGN_PARAGRAPH.CENTER, 3: WD_ALIGN_PARAGRAPH.CENTER,
              4: WD_ALIGN_PARAGRAPH.CENTER, 5: WD_ALIGN_PARAGRAPH.CENTER}
    col_tags = {2: "[A]", 3: "[B]", 4: "[C]", 5: "[D]"}
    _basic_table(doc, ["Code", "Product"] + cols, rows, [1.1, 6.1, 2.45, 2.45, 2.45, 2.45],
                 body_size=TBL_COMPACT_SZ, aligns=aligns, col_tags=col_tags)


def _grid_table(doc, items, grid_cols, cell_text):
    if not grid_cols:
        grid_cols = [{"code": "A", "label": "[COLUMN TBU]", "logic": ""}]
    headers = ["Code", "Row"] + [c["label"] for c in grid_cols]
    rows = [[it["code"], _label_with_logic(it)] + [cell_text] * len(grid_cols) for it in items]
    fixed = [1.0, 6.3]; per = (DOC_W_CM - sum(fixed)) / len(grid_cols)
    aligns = {0: WD_ALIGN_PARAGRAPH.CENTER}
    col_tags = {}
    letters = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    for k in range(len(grid_cols)):
        aligns[2 + k] = WD_ALIGN_PARAGRAPH.CENTER
        col_tags[2 + k] = f"[{letters[k]}]" if k < 26 else None
    _basic_table(doc, headers, rows, fixed + [per] * len(grid_cols), body_size=TBL_COMPACT_SZ,
                 aligns=aligns, col_tags=col_tags)


def _rating_table(doc, items, anchors):
    n = 7
    tbl = doc.add_table(rows=2, cols=2 + n); tbl.alignment = WD_TABLE_ALIGNMENT.LEFT
    # super-header (anchors) — merge groups 1-2, 3-5, 6-7
    sr = tbl.rows[0].cells
    sr[0].merge(sr[1])
    groups = [(2, 3, anchors[0]), (4, 6, anchors[1]), (7, 8, anchors[2])]
    for a, b, label in groups:
        merged = sr[a].merge(sr[b])
        _hdr_cell(merged, label, size=TBL_COLID_SZ)
    _hdr_cell(sr[0], "")
    # number header row with [A]..[G]
    hr = tbl.rows[1].cells
    _hdr_cell(hr[0], "No.", size=TBL_COMPACT_SZ)
    _hdr_cell(hr[1], "Statement", size=TBL_COMPACT_SZ, align=WD_ALIGN_PARAGRAPH.LEFT)
    letters = "ABCDEFG"
    for j in range(n):
        _hdr_cell(hr[2 + j], str(j + 1), size=TBL_COMPACT_SZ, tag=f"[{letters[j]}]")
    for idx, it in enumerate(items, start=1):
        cells = tbl.add_row().cells
        _cell(cells[0], str(idx), size=TBL_COMPACT_SZ, align=WD_ALIGN_PARAGRAPH.CENTER)
        _cell(cells[1], _label_with_logic(it), size=TBL_COMPACT_SZ)
        for j in range(n):
            _cell(cells[2 + j], "O", size=TBL_COMPACT_SZ, align=WD_ALIGN_PARAGRAPH.CENTER)
    _set_borders(tbl); _widths(tbl, [0.8, 8.2] + [1.14] * n)


def _bipolar_table(doc, pairs):
    n = 7
    tbl = doc.add_table(rows=2, cols=3 + n); tbl.alignment = WD_TABLE_ALIGNMENT.LEFT
    sr = tbl.rows[0].cells
    sr[0].merge(sr[1])
    for a, b, label in [(2, 3, "Agree with A"), (4, 6, "Neutral"), (7, 8, "Agree with B")]:
        _hdr_cell(sr[a].merge(sr[b]), label, size=TBL_COLID_SZ)
    _hdr_cell(sr[0], "")
    hr = tbl.rows[1].cells
    _hdr_cell(hr[0], "No.", size=8.2); _hdr_cell(hr[1], "Statement A", size=8.2, align=WD_ALIGN_PARAGRAPH.LEFT)
    letters = "ABCDEFG"
    for j in range(n):
        _hdr_cell(hr[2 + j], str(j + 1), size=8.2, tag=f"[{letters[j]}]")
    _hdr_cell(hr[2 + n], "Statement B", size=8.2, align=WD_ALIGN_PARAGRAPH.LEFT)
    for idx, pr in enumerate(pairs, start=1):
        cells = tbl.add_row().cells
        _cell(cells[0], str(idx), size=8.2, align=WD_ALIGN_PARAGRAPH.CENTER)
        _cell(cells[1], pr["left"], size=8.2)
        for j in range(n):
            _cell(cells[2 + j], "O", size=8.2, align=WD_ALIGN_PARAGRAPH.CENTER)
        _cell(cells[2 + n], pr["right"], size=8.2)
    _set_borders(tbl); _widths(tbl, [0.65, 3.9] + [1.1] * n + [3.9])


def _small_table(doc, headers, rows):
    cm = []
    for h in headers:
        hl = h.lower()
        if re.search(r"s\.no|code|id|symbol|metric|status|target", hl):
            cm.append(1.1)
        elif re.search(r"meaning|criteria|objective|notes|response|option|content|value|qualification|recommendation|example", hl):
            cm.append(4.0)
        else:
            cm.append(2.5)
    _basic_table(doc, headers, rows, cm, body_size=9)


# ---- programming tags (port of add_programming_lines, minus VALIDATION) -----
def _terminating_codes(items):
    """Option codes that carry an inline {TERMINATE}/{SCREEN OUT} marker."""
    return [it["code"] for it in items if it.get("logic") and _TERM_RX.search(it["logic"])]


def _is_option_termination(line, codes, items):
    """True if `line` just restates a termination already owned by a terminating option
    (so it can be dropped in favour of the canonical per-option line). Non-option
    terminations — e.g. numeric thresholds — return False and are preserved."""
    low = line.lower()
    for c in codes:
        cl = c.lower()
        if re.search(rf"\[\s*{re.escape(cl)}\s*\]", low):          # [1]
            return True
        if re.search(rf"\boption\s+{re.escape(cl)}\b", low):       # option 1
            return True
        if re.search(rf"(^|[^\w]){re.escape(cl)}\s*\|", low):      # "1 | ..."
            return True
    for it in items:
        if it["code"] in codes:
            lab = it["label"].strip().lower()
            if len(lab) >= 8 and lab[:28] in low:                  # matches by option label
                return True
    # "...any of options 1, 2, 3, 4, or 5" — a plural list that's entirely terminating options
    if re.search(r"\boptions?\b", low):
        nums = re.findall(r"\d+", line)
        if nums and all(n in codes for n in nums):
            return True
    return False


def _format_codes(codes):
    if len(codes) == 1:
        return f"option {codes[0]}"
    return "options " + ", ".join(codes[:-1]) + " or " + codes[-1]


def _is_term_line(x):
    """Any line that expresses a termination (in ANY phrasing: 'terminate', 'screen out',
    'code N selected -> TERMINATE', 'response = N', ...) — all contain the keyword."""
    return bool(_TERM_RX.search(x))


def _is_terminate_trigger_show(line, term_codes, has_term):
    """A skip/option-logic CONDITION line that is really just the terminate trigger restated
    (e.g. 'SHOW LOGIC: code 1 selected', 'entry < 1'). Only applied to condition fields —
    never to programming directives like ANCHOR/FORCE/RECORD."""
    if not has_term:
        return False
    low = line.lower()
    if term_codes:
        nums = set(re.findall(r"\d+", line))
        if nums and nums <= set(term_codes) and re.search(r"select|\bcode\b|\boption\b", low):
            return True
    if re.search(r"\b(entry|response|value)\s*[<>=]", low):   # numeric threshold restated
        return True
    return False


def _clean_terminations(q):
    """The ONLY termination lines that survive. Collapses the model's many redundant
    phrasings ('code 1 selected', 'response = 1', 'code 1 selected -> TERMINATE', ...)
    into canonical output:
      - terminating options -> per-option (1-2) or one combined line (3+);
      - genuinely distinct (threshold/other) terminations kept, deduped to one.
    Shared by the per-question programming tags and the Screening Criteria table."""
    term_codes = _terminating_codes(q["items"])
    out = []
    if term_codes:
        if len(term_codes) > 2:
            out.append(f"TERMINATE IF {_format_codes(term_codes)} selected")  # collapse the wall
        else:
            out += [f"TERMINATE IF option {c} selected" for c in term_codes]  # per-option for 1-2

    distinct = []
    for x in (q["termination_logic"] + q["programming"]):
        if not _is_term_line(x) or _META_RX.search(x):
            continue
        if "->" in x or "→" in x:                              # arrow-style restatement
            continue
        if term_codes and _is_option_termination(x, term_codes, q["items"]):
            continue                                                # restates an option termination
        if term_codes:
            nums = set(re.findall(r"\d+", x))
            if nums and nums <= set(term_codes):                    # 'code 1', 'response = 1', ... = restatement
                continue
        if x not in distinct:
            distinct.append(x)
    if not term_codes and len(distinct) > 1:
        distinct = distinct[:1]                                     # phrasings of one threshold -> keep clearest
    out += distinct

    seen, res = set(), []
    for t in out:
        t = t.strip()
        if t and t not in seen:
            seen.add(t); res.append(t)
    return res


def _programming_lines(doc, q):
    """Render the logic block. Genuine directives (ANCHOR/FORCE/RECORD/RANDOMIZE/skip) are
    kept; every terminate-flavored line (any phrasing) and every condition line that merely
    restates a terminate trigger are dropped, then replaced by canonical terminations."""
    term_codes = _terminating_codes(q["items"])
    has_term = bool(term_codes) or any(
        _is_term_line(x) for x in (q["termination_logic"] + q["programming"] + q["option_logic"]))
    lines = []

    def keep_directive(x, prefix="", check_trigger=False):
        if _META_RX.search(x):
            return
        if _is_term_line(x):                                        # terminations -> canonical only
            return
        if check_trigger and _is_terminate_trigger_show(x, term_codes, has_term):
            return                                                  # condition that just restates a terminate trigger
        lines.append(f"{prefix}{x}")

    for x in q["programming"]:
        keep_directive(x)                                           # imperative directives: never trigger-filtered
    for x in q["option_logic"]:
        keep_directive(x, "OPTION LOGIC: ", check_trigger=True)
    for x in q["skip_logic"]:
        keep_directive(x, "SHOW LOGIC: ", check_trigger=True)
    for c in q["grid_cols"]:
        if c.get("logic"):
            lines.append(f"GRID COLUMN LOGIC {c['code']}: {c['logic'].replace(chr(10), '; ')}")

    # consolidated terminations (one per terminating option + distinct non-option ones)
    lines += _clean_terminations(q)

    # NOTE: q['validation'] is intentionally omitted — no [VALIDATION: ...] tags.
    seen, out = set(), []
    for ln in lines:
        ln = ln.strip()
        if ln and ln not in seen:
            seen.add(ln); out.append(ln)
    for ln in out:
        _tag_line(doc, ln)


def _q_header(doc, q):
    _qtype_tag(doc, q["question_type"])
    qid = q["question_id"] or "[QUESTION ID TBU]"
    qtext = q["question_text"] or "[QUESTION TEXT TBU]"
    _para(doc, _R(f"{qid}: ", size=Q_ID_SZ, bold=True, color=DARK),
          _R(qtext, size=Q_TXT_SZ, color=DARK), line_spacing=1.25, outline_level=1)
    if q["notes"] and not _META_RX.search(q["notes"]):
        _para(doc, _R(f"NOTE: {q['notes']}", size=NOTE_SZ, bold=True, color=NOTE_GREY))


def _end_block(doc):
    doc.add_paragraph()
    _tag_line(doc, "PAGE BREAK")
    doc.add_paragraph()


# ---- per-type renderers (port of render_*) ----------------------------------
def _render_question(doc, q):
    qt = q["question_type"]
    if not q["question_id"] or qt not in SUPPORTED or (not q["question_text"] and qt not in ("INSTRUCTION", "TPP_DISPLAY")):
        _tag_line(doc, f"QUESTION TBU_INCOMPLETE: {q['question_id'] or 'UNKNOWN'}")
        _end_block(doc); return
    _q_header(doc, q)
    items = _items_or_ph(q["items"])
    if qt == "SINGLE_SELECT":
        _resp_instr(doc, "Select one."); _option_table(doc, items, "O", "Response")
    elif qt == "MULTI_SELECT":
        _resp_instr(doc, "Select all that apply."); _option_table(doc, items, CHECKBOX, "Response")
    elif qt == "RANKING":
        _resp_instr(doc, "Rank the items below. Rank 1 is highest."); _option_table(doc, items, "__", "Rank")
    elif qt in RATING_TYPES:
        _resp_instr(doc, "Select one response per row."); _rating_table(doc, _items_or_ph(q["items"], "[STATEMENT TBU]"), _anchors(qt))
    elif qt == "RATING_DUAL":
        _resp_instr(doc, "Select the point that best reflects where you fall between each pair of statements.")
        _bipolar_table(doc, q["dual_pairs"] or [{"code": "1", "left": "[LEFT TBU]", "right": "[RIGHT TBU]", "logic": ""}])
    elif qt == "NUMERIC":
        _resp_instr(doc, "Enter a whole number."); _entry_table(doc, q["items"] or [{"code": "1", "label": "Response", "logic": ""}], NUMERIC_PH, "Entry")
    elif qt == "PERCENT_ALLOCATION":
        _resp_instr(doc, "Enter whole-number percentages. Total must sum to 100%."); _percent_table(doc, _items_or_ph(q["items"], "[ALLOCATION ROW TBU]"))
    elif qt == "AWARENESS_USAGE":
        _resp_instr(doc, "Select one response for each product or treatment."); _awareness_table(doc, _items_or_ph(q["items"], "[PRODUCT TBU]"))
    elif qt == "GRID":
        _resp_instr(doc, "Complete the grid below."); _grid_table(doc, _items_or_ph(q["items"], "[ROW TBU]"), q["grid_cols"], "O")
    elif qt == "DROPDOWN":
        _resp_instr(doc, "Select from the dropdown menus below.")
        _grid_table(doc, _items_or_ph(q["items"], "[ROW TBU]"), q["grid_cols"], "[INSERT Dropdown_1]")
        if q["dropdown_options"]:
            _tag_line(doc, "Dropdown_1 OPTIONS")
            _small_table(doc, ["Code", "Option"], [[o["code"], _label_with_logic(o)] for o in q["dropdown_options"]])
        else:
            _tag_line(doc, "Dropdown_1 OPTIONS TBU")
    elif qt == "OPEN_END":
        _resp_instr(doc, "Please type your response."); _entry_table(doc, q["items"] or [{"code": "1", "label": "Response", "logic": ""}], OPENEND_PH, "Open end")
    elif qt == "TPP_DISPLAY":
        _tag_line(doc, "INSERT LINK TO PRODUCT TPP"); _tag_line(doc, "FORCE RESPONDENT TO VIEW FOR AT LEAST 20 SECONDS")
        if q["items"]:
            _small_table(doc, ["TPP_Content"], [[it["label"]] for it in q["items"]])
    elif qt == "INSTRUCTION":
        for it in q["items"]:
            _para(doc, _R(it["label"], size=SMALL_SZ, color=DARK), line_spacing=1.25)
    _programming_lines(doc, q)
    _end_block(doc)


def _anchors(qtype):
    return {
        "RATING_7_IMPORTANCE": ("Not at all important", "Somewhat important", "Very important"),
        "RATING_7_SATISFACTION": ("Not at all satisfied", "Neutral", "Extremely satisfied"),
        "RATING_7_LIKELIHOOD": ("Definitely would not", "Might or might not", "Definitely would"),
        "RATING_7_FAMILIARITY": ("Not at all familiar", "Moderately familiar", "Highly familiar"),
    }.get(qtype, ("Strongly disagree", "Neither agree nor disagree", "Strongly agree"))


# ---- front matter (port of add_*) -------------------------------------------
def _front_title(doc, text):
    _para(doc, _R(text, size=FRONT_TITLE_SZ, bold=True, color=DARK), outline_level=0)


def _cover(doc, meta):
    g = lambda k, d="": meta.get(k, d) or d
    _para(doc, _R(g("Study_Title", "[STUDY TITLE TBU]"), size=COVER_TITLE_SZ, bold=True, color=ORANGE))
    doc.add_paragraph()
    _para(doc, _R(g("Draft_Label", "Draft Questionnaire"), size=FRONT_TITLE_SZ, bold=True, color=DARK))
    doc.add_paragraph()
    _para(doc, _R(f"Prepared for {g('Client_Name', '[CLIENT TBU]')}", size=COVER_CLIENT_SZ, color=DARK))
    _para(doc, _R(f"Version: {g('Version_Label', 'v1.0')}", size=COVER_META_SZ, color=DARK))
    _para(doc, _R(f"Last updated: {g('Last_Updated_Date', date.today().isoformat())}", size=COVER_META_SZ, color=DARK))
    doc.add_paragraph()
    _para(doc, _R(g("Prepared_By", "ZS Associates"), size=COVER_CLIENT_SZ, bold=True, color=DARK))
    doc.add_page_break()


def _programming_instructions(doc):
    _front_title(doc, "Programming Instructions")
    legend = [
        ["O", "Allow only one selection. Selected = 1, not selected = 0."],
        ["Checkbox", "Select all that apply. Selected = 1, not selected = 0."],
        [NUMERIC_PH, "Numeric whole-number entry. Do not accept ranges."],
        [PERCENT_PH, "Whole-number percentage between 0 and 100. Do not accept ranges."],
        ["[INSERT Dropdown_1]", "Dropdown option inserted by survey programmer."],
        [OPENEND_PH, "Open-ended text response."],
        ["{SHOW IF ...}", "Inline item-level programming logic; do not show to respondents."],
    ]
    _small_table(doc, ["Symbol", "Meaning"], legend)
    for ln in [
        "Do not show blue/grey programming tags online.",
        "Do not show section headers, question numbers, or response labels online unless explicitly specified.",
        "Keep Other, I don't know, None of the above, and Total anchored at the bottom where applicable.",
        "None, None of the above, and I don't know should be mutually exclusive with other selections.",
        "Respondents must answer each question before continuing unless noted otherwise.",
        "Between questions, the draft displays a [PAGE BREAK] tag; actual Word page breaks begin each new section.",
    ]:
        _tag_line(doc, ln)
    doc.add_page_break()


def _sample_plan(doc, sample_plan):
    _front_title(doc, "Sample Plan")
    rows = [[s["Segment_ID"], s["Audience_Segment"], s["Target_N"], s["Qualification_Notes"]] for s in sample_plan] \
        or [["[TBU]", "[SAMPLE PLAN TBU]", "", ""]]
    _small_table(doc, ["Segment_ID", "Audience_Segment", "Target_N", "Qualification_Notes"], rows)
    doc.add_page_break()


def _screening_criteria(doc, questions):
    _front_title(doc, "Screening Criteria")
    rows = []
    for q in questions:
        if (q["section_id"] or "").upper() == "SCR":
            terms = _clean_terminations(q)
            if terms:
                rows.append([q["question_id"], "\n".join(terms)])
    if not rows:
        rows = [["[TBU]", "[SCREENING CRITERIA TBU]"]]
    rows = [[str(i + 1), r[0], r[1]] for i, r in enumerate(rows)]
    _small_table(doc, ["S.No.", "Question_ID", "Criteria"], rows)
    doc.add_page_break()


def _outline_summary(doc, questions):
    _front_title(doc, "Survey Outline")
    seen, rows = set(), []
    for q in questions:
        key = (q["section_id"], q["section_title"])
        if key not in seen:
            seen.add(key); rows.append([q["section_id"], q["section_title"], q["section_objective"]])
    _small_table(doc, ["Section_ID", "Section_Title", "Section_Objective"], rows or [["[TBU]", "[TBU]", "[TBU]"]])
    doc.add_page_break()


def _disclosures(doc, meta):
    _front_title(doc, "Disclosures and Consent")
    lines = [
        "This survey is being conducted for market research purposes only.",
        "Your responses will be reported in aggregate and will not be used for promotional purposes.",
        "Please do not include patient-identifying information in any open-ended response.",
        "If you mention an adverse event, it may need to be reported according to applicable regulations.",
    ]
    def _on(k, d): return str(meta.get(k, "YES" if d else "NO")).strip().lower() in ("y", "yes", "true", "1", "include")
    if _on("Include_AI_Disclosure", True):
        lines.append("Artificial intelligence may be used to support analysis of anonymized and aggregated research inputs.")
    if _on("Include_Recontact", True):
        lines.append("You may be asked for permission to be re-contacted for follow-up research.")
    for ln in lines:
        _para(doc, _R(ln, size=SMALL_SZ, color=DARK), line_spacing=1.25)
    doc.add_paragraph()
    _small_table(doc, ["Code", "Response", "Select"], [["1", "I consent", "O"], ["2", "I do not consent - Exit Survey", "O"]])
    doc.add_page_break()


def _intro(doc, meta):
    _front_title(doc, "Respondent Introduction")
    txt = ("Thank you for participating in this market research survey. This survey is intended for "
           f"{meta.get('Audience_Description', '[AUDIENCE TBU]')}. The survey is expected to take approximately "
           f"{meta.get('Estimated_Length_Minutes', '[LENGTH TBU]')} minutes.")
    _para(doc, _R(txt, size=SMALL_SZ, color=DARK), line_spacing=1.25)
    doc.add_page_break()


# ---- main -------------------------------------------------------------------
def render_questionnaire_docx(workbook_path: str, out_path: str) -> str:
    meta, sample_plan, questions = _read_workbook(workbook_path)

    doc = Document()
    st = doc.styles["Normal"]; st.font.name = FONT; st.font.size = Pt(SMALL_SZ)
    for s in doc.sections:
        s.page_width = Cm(21.0); s.page_height = Cm(29.7)  # A4 (officer default)
        s.left_margin = s.right_margin = Cm(2.25); s.top_margin = s.bottom_margin = Cm(2.0)

    _cover(doc, meta)
    _programming_instructions(doc)
    _sample_plan(doc, sample_plan)
    _screening_criteria(doc, questions)
    _outline_summary(doc, questions)
    _disclosures(doc, meta)
    _intro(doc, meta)

    current = None
    for q in questions:
        key = (q["section_id"], q["section_title"])
        if key != current:
            if current is not None:
                doc.add_page_break()
            current = key
            _para(doc, _R(q["section_title"] or q["section_id"], size=SECTION_TITLE_SZ, bold=True, color=DARK),
                  outline_level=0)
            if q["section_objective"]:
                _para(doc, _R(q["section_objective"], size=NOTE_SZ, color=NOTE_GREY), line_spacing=1.25)
            doc.add_paragraph()
        _render_question(doc, q)

    secs = len({(q["section_id"], q["section_title"]) for q in questions})
    _front_title(doc, "Render Summary")
    _small_table(doc, ["Metric", "Value"], [["Questions", str(len(questions))],
                 ["Sections", str(secs)], ["Output_File", out_path.replace("\\", "/").split("/")[-1]]])

    doc.save(out_path)
    return out_path
