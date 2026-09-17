"""Strict CSV contract helpers for Phase 1 hypothesis generation.

The generator prompt intentionally asks for one simple text shape instead of
provider-specific structured output:

    hypotheses number,category,Hypotheses,rationale

These helpers parse that shape defensively and report actionable diagnostics so
Phase 1 can retry or fall back to editable rows instead of blocking the user.
"""
from __future__ import annotations

import csv
import io
import json
import re
from dataclasses import dataclass, field
from typing import Any, Iterable

from survey_generator.runtime import sanitize_text, shorten_text

CANONICAL_HEADERS = ["hypotheses number", "category", "Hypotheses", "rationale"]

_HEADER_ALIASES = {
    "hypothesis_id": {
        "hypotheses number", "hypothesis number", "hypothesis_number", "hypotheses_number",
        "hypothesis id", "hypothesis_id", "hyp id", "hyp_id", "id", "number", "#",
    },
    "category": {"category", "framework", "theme", "domain", "hypothesis category", "hypothesis_category"},
    "text": {"hypotheses", "hypothesis", "hypothesis text", "hypothesis_text", "text", "statement", "claim"},
    "rationale": {"rationale", "reason", "why", "logic", "support", "supporting rationale"},
}

_TEXT_KEYS = ("text", "hypothesis", "hypotheses", "hypothesis_text", "statement", "claim", "research_hypothesis")
_CATEGORY_KEYS = ("category", "framework", "theme", "domain", "hypothesis_category")
_ID_KEYS = ("hypotheses number", "hypothesis_number", "hypotheses_number", "hypothesis_id", "id", "hyp_id")
_CONTAINER_KEYS = ("hypotheses", "research_hypotheses", "items", "results", "data", "output", "answer")


@dataclass
class CSVParseResult:
    rows: list[dict[str, str]] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)
    raw_preview: str = ""
    detected_format: str = "unknown"

    @property
    def ok(self) -> bool:
        return bool(self.rows)


def canonical_header_line() -> str:
    return ",".join(CANONICAL_HEADERS)


def csv_example_rows(categories: Iterable[str] | None = None) -> str:
    cats = [str(c).strip() for c in categories or [] if str(c).strip()] or ["Potential"]
    sample_cat = cats[0]
    output = io.StringIO()
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(CANONICAL_HEADERS)
    writer.writerow([
        "H001",
        sample_cat,
        "HCPs who perceive the product evidence as credible and differentiated are more likely to be receptive to adoption.",
        "Tests whether evidence credibility separates higher-receptivity segments and guides message strategy.",
    ])
    writer.writerow([
        "H002",
        sample_cat,
        "HCPs who expect access, affordability, or workflow barriers are less likely to prescribe the target product routinely.",
        "Tests whether practical barriers suppress adoption even when clinical interest exists.",
    ])
    return output.getvalue().strip()


def _norm_header(value: Any) -> str:
    text = sanitize_text(value).strip().lower().replace("_", " ").replace("-", " ")
    text = re.sub(r"\s+", " ", text)
    return text


def _canonical_for_header(value: Any) -> str | None:
    norm = _norm_header(value)
    for canonical, aliases in _HEADER_ALIASES.items():
        if norm in aliases:
            return canonical
    return None


def _normalise_id(value: Any, idx: int) -> str:
    raw = sanitize_text(value).strip()
    if not raw:
        return f"H{idx:03d}"
    if raw.upper().startswith("H"):
        digits = re.sub(r"\D", "", raw)
        return f"H{int(digits):03d}" if digits else raw.upper()
    digits = re.sub(r"\D", "", raw)
    return f"H{int(digits):03d}" if digits else raw


def _strip_fences_and_preamble(raw: str) -> str:
    text = sanitize_text(raw or "").strip("\ufeff\n\r \t")
    # Remove complete markdown fences if present.
    fenced = re.match(r"^```(?:csv|text|txt)?\s*(.*?)\s*```$", text, flags=re.IGNORECASE | re.DOTALL)
    if fenced:
        text = fenced.group(1).strip()
    lines = [ln.rstrip() for ln in text.splitlines() if ln.strip()]
    if not lines:
        return ""
    # Start at the first likely header line; models sometimes add one sentence
    # before the CSV despite instructions.
    for i, ln in enumerate(lines):
        lowered = _norm_header(ln)
        if "category" in lowered and "rationale" in lowered and ("hypoth" in lowered or "statement" in lowered):
            return "\n".join(lines[i:]).strip()
    return "\n".join(lines).strip()


def _parse_pipe_table(text: str) -> list[list[str]]:
    rows: list[list[str]] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("|") or "|" not in stripped[1:]:
            continue
        cells = [c.strip() for c in stripped.strip("|").split("|")]
        if cells and all(re.fullmatch(r":?-{3,}:?", c or "") for c in cells):
            continue
        rows.append(cells)
    return rows


def _rows_with_delimiter(text: str, delimiter: str) -> list[list[str]]:
    try:
        reader = csv.reader(io.StringIO(text), delimiter=delimiter)
        return [[sanitize_text(c).strip() for c in row] for row in reader if any(str(c).strip() for c in row)]
    except csv.Error:
        return []


def _choose_table(text: str) -> tuple[list[list[str]], str]:
    candidates: list[tuple[list[list[str]], str]] = []
    for delimiter, label in [(",", "csv"), ("\t", "tsv"), (";", "semicolon")]:
        rows = _rows_with_delimiter(text, delimiter)
        if rows:
            candidates.append((rows, label))
    pipe_rows = _parse_pipe_table(text)
    if pipe_rows:
        candidates.append((pipe_rows, "markdown_table"))

    def score(rows: list[list[str]]) -> int:
        if not rows:
            return 0
        header = [_canonical_for_header(c) for c in rows[0]]
        header_score = len({h for h in header if h}) * 10
        width_score = max((len(r) for r in rows), default=0)
        return header_score + width_score + min(len(rows), 20)

    if not candidates:
        return [], "unknown"
    return max(candidates, key=lambda item: score(item[0]))


def _header_map(header: list[str]) -> dict[str, int]:
    out: dict[str, int] = {}
    for idx, cell in enumerate(header):
        canonical = _canonical_for_header(cell)
        if canonical and canonical not in out:
            out[canonical] = idx
    return out


def _infer_overwide_row(row: list[str], allowed_categories: list[str]) -> list[str]:
    """Recover common malformed CSV where commas inside fields were not quoted."""
    cells = [sanitize_text(c).strip() for c in row]
    if len(cells) <= 4:
        return cells + [""] * (4 - len(cells))
    # First cell is the ID. Find a category spanning one or more cells, then
    # treat the final cell as rationale and the middle as the hypothesis text.
    allowed = {c.strip(): c.strip() for c in allowed_categories if c.strip()}
    for end in range(2, min(len(cells), 6)):
        candidate = ",".join(cells[1:end]).strip()
        candidate_spaced = ", ".join(cells[1:end]).strip()
        category = allowed.get(candidate) or allowed.get(candidate_spaced)
        if category:
            return [cells[0], category, ",".join(cells[end:-1]).strip(), cells[-1]]
    return [cells[0], cells[1], ",".join(cells[2:-1]).strip(), cells[-1]]


def _row_from_cells(cells: list[str], header_map: dict[str, int], idx: int, allowed_categories: list[str]) -> dict[str, str] | None:
    if header_map:
        # If fields containing commas were not quoted, csv.reader produces an
        # over-wide row. Recover positionally where possible instead of taking
        # the wrong cells from the fixed header positions.
        if len(cells) > 4:
            fixed = _infer_overwide_row(cells, allowed_categories)
            hid, category, text, rationale = fixed[0], fixed[1], fixed[2], fixed[3]
        else:
            def get(name: str) -> str:
                pos = header_map.get(name)
                return sanitize_text(cells[pos]).strip() if pos is not None and pos < len(cells) else ""
            hid = get("hypothesis_id")
            category = get("category")
            text = get("text")
            rationale = get("rationale")
    else:
        fixed = _infer_overwide_row(cells, allowed_categories)
        hid, category, text, rationale = fixed[0], fixed[1], fixed[2], fixed[3]

    text = re.sub(r"\s+", " ", sanitize_text(text)).strip()
    if not text or _norm_header(text) in _HEADER_ALIASES["text"]:
        return None
    return {
        "hypothesis_id": _normalise_id(hid, idx),
        "category": re.sub(r"\s+", " ", sanitize_text(category)).strip(),
        "text": text,
        "rationale": re.sub(r"\s+", " ", sanitize_text(rationale)).strip(),
    }


def _first_present(row: dict[str, Any], keys: Iterable[str]) -> Any:
    for key in keys:
        if key in row and row.get(key) not in (None, ""):
            return row.get(key)
    return ""


def _json_candidate_records(data: Any, category_hint: str = "") -> list[dict[str, str]]:
    records: list[dict[str, str]] = []
    if isinstance(data, str):
        text = data.strip()
        if text:
            records.append({"text": text, "category": category_hint})
        return records
    if isinstance(data, list):
        for item in data:
            records.extend(_json_candidate_records(item, category_hint=category_hint))
        return records
    if isinstance(data, dict):
        if any(str(data.get(k, "")).strip() for k in _TEXT_KEYS):
            records.append({
                "hypothesis_id": str(_first_present(data, _ID_KEYS)).strip(),
                "category": str(_first_present(data, _CATEGORY_KEYS) or category_hint).strip(),
                "text": str(_first_present(data, _TEXT_KEYS)).strip(),
                "rationale": str(data.get("rationale", "")).strip(),
            })
            return records
        for key in _CONTAINER_KEYS:
            if key in data:
                records.extend(_json_candidate_records(data.get(key), category_hint=category_hint))
        for key, value in data.items():
            if key in _CONTAINER_KEYS or key in {"metadata", "notes", "schema"}:
                continue
            if isinstance(value, (list, dict, str)):
                records.extend(_json_candidate_records(value, category_hint=category_hint or str(key)))
    return records


def _try_json_fallback(text: str) -> list[dict[str, str]]:
    stripped = text.strip()
    # Only attempt JSON fallback when the response actually looks like JSON.
    if not stripped or stripped[0] not in "[{":
        return []
    try:
        data = json.loads(stripped)
    except json.JSONDecodeError:
        return []
    out: list[dict[str, str]] = []
    for idx, row in enumerate(_json_candidate_records(data), start=1):
        if row.get("text"):
            out.append({
                "hypothesis_id": _normalise_id(row.get("hypothesis_id", ""), idx),
                "category": sanitize_text(row.get("category", "")).strip(),
                "text": sanitize_text(row.get("text", "")).strip(),
                "rationale": sanitize_text(row.get("rationale", "")).strip(),
            })
    return out


def parse_hypothesis_csv_response(raw: str, *, allowed_categories: Iterable[str] | None = None) -> CSVParseResult:
    """Parse the strict four-column hypothesis CSV response.

    The parser is intentionally forgiving around markdown fences, accidental
    preamble text, tabs, semicolons, markdown tables, and over-wide comma rows,
    but it returns only the canonical four fields used by Phase 1.
    """
    raw_preview = shorten_text(raw or "", 700)
    text = _strip_fences_and_preamble(raw or "")
    allowed = [str(c).strip() for c in allowed_categories or [] if str(c).strip()]
    issues: list[str] = []
    if not text:
        return CSVParseResult(issues=["Model response was empty."], raw_preview=raw_preview)

    table, detected = _choose_table(text)
    rows: list[dict[str, str]] = []
    if table:
        header_map = _header_map(table[0])
        missing = [name for name in ("hypothesis_id", "category", "text", "rationale") if name not in header_map]
        data_rows = table[1:] if not missing else table
        if missing:
            issues.append(
                "CSV header did not exactly match the required four columns; attempted position-based recovery."
            )
            header_map = {}
        for idx, cells in enumerate(data_rows, start=1):
            row = _row_from_cells(cells, header_map, idx, allowed)
            if row:
                rows.append(row)
    if not rows:
        json_rows = _try_json_fallback(text)
        if json_rows:
            rows = json_rows
            detected = "json_fallback"
            issues.append("Model returned JSON instead of the requested CSV; recovered rows but the prompt will be retried next time.")

    if not rows:
        issues.append(
            "No usable hypothesis rows found. Expected header: " + canonical_header_line()
        )
    else:
        for row in rows:
            # The category column is intentionally a construct/dimension, not a
            # framework title. `allowed` is used only for malformed-row recovery;
            # do not reject custom category text here.
            if not row.get("rationale"):
                issues.append("At least one row is missing a rationale.")
                break

    return CSVParseResult(rows=rows, issues=issues, raw_preview=raw_preview, detected_format=detected)


def csv_retry_instruction(parse_result: CSVParseResult, allowed_categories: Iterable[str] | None = None) -> str:
    cats = [str(c).strip() for c in allowed_categories or [] if str(c).strip()]
    cat_text = ", ".join(cats) if cats else "the selected categories"
    issues = "; ".join(parse_result.issues) or "The response was not usable."
    return (
        "\n\nYour previous response could not be parsed as the required CSV. "
        f"Issue: {shorten_text(issues, 500)}\n"
        "Return ONLY this four-column CSV. No markdown, no bullets, no commentary.\n"
        f"Header must be exactly:\n{canonical_header_line()}\n"
        f"Use the category column for a specific construct/dimension such as: {cat_text}. Do not put the framework name in category.\n"
        "Quote any field that contains a comma."
    )


def rows_to_simple_hypothesis_csv(rows: Iterable[dict[str, Any]]) -> str:
    """Serialize hypothesis rows to the simple four-column CSV contract."""
    output = io.StringIO()
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(CANONICAL_HEADERS)
    for idx, row in enumerate(rows or [], start=1):
        if not isinstance(row, dict):
            continue
        text = sanitize_text(row.get("text") or row.get("Hypotheses") or row.get("hypothesis") or "").strip()
        if not text:
            continue
        writer.writerow([
            _normalise_id(row.get("hypothesis_id") or row.get("hypotheses number") or row.get("id"), idx),
            sanitize_text(row.get("category", "")).strip(),
            re.sub(r"\s+", " ", text).strip(),
            re.sub(r"\s+", " ", sanitize_text(row.get("rationale", ""))).strip(),
        ])
    return output.getvalue()
