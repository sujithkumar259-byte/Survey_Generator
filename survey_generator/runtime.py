"""
Runtime utilities for production-safe Streamlit sessions.

This module keeps app-wide operational concerns out of the UI code:
project/session IDs, per-session folders, upload limits, cleanup, safe filenames,
and lightweight structured logging.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence


APP_TEMP_ROOT = Path(
    os.environ.get("SURVEY_GENERATOR_TEMP_ROOT", str(Path(tempfile.gettempdir()) / "survey_generator"))
)
DEFAULT_RETENTION_HOURS = int(os.environ.get("FILE_RETENTION_HOURS", "24"))
MAX_UPLOAD_MB = int(os.environ.get("MAX_UPLOAD_MB", "25"))
MAX_TOTAL_UPLOAD_MB = int(os.environ.get("MAX_TOTAL_UPLOAD_MB", str(MAX_UPLOAD_MB * 4)))

MAX_UPLOAD_BYTES = MAX_UPLOAD_MB * 1024 * 1024
MAX_TOTAL_UPLOAD_BYTES = MAX_TOTAL_UPLOAD_MB * 1024 * 1024

SUPPORTED_SOURCE_EXTENSIONS = ["pdf", "docx", "pptx", "xlsx", "csv", "txt", "md"]
SUPPORTED_HYPOTHESIS_EXTENSIONS = ["xlsx", "csv", "txt", "md"]
SUPPORTED_WORKBOOK_EXTENSIONS = ["xlsx"]

PROJECT_SUBDIRS = ("uploads", "outputs", "logs", "intermediate")

# OpenXML/Excel cannot store most ASCII control characters. PPTX/PDF text
# extraction sometimes returns invisible characters such as vertical-tab (\x0b),
# which can otherwise crash report exports with openpyxl.IllegalCharacterError.
ILLEGAL_XML_CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0B\x0C\x0E-\x1F]")
EXCEL_CELL_CHAR_LIMIT = 32767


def sanitize_text(value: Any, *, replacement: str = " ") -> str:
    """Return text safe for UI display, logs, OpenXML, and Excel cells.

    Keeps normal newlines/tabs but removes control characters that are illegal
    in XML 1.0 / XLSX. This deliberately accepts any value so callers can use
    it at workbook boundaries without defensive type checks.
    """
    if value is None:
        return ""
    text = value if isinstance(value, str) else str(value)
    text = ILLEGAL_XML_CONTROL_CHARS_RE.sub(replacement, text)
    return text.replace("\ufffe", "").replace("\uffff", "")


def shorten_text(value: Any, limit: int = 240, *, collapse_whitespace: bool = True) -> str:
    """Sanitize and shorten text for non-blocking UI summaries."""
    text = sanitize_text(value)
    if collapse_whitespace:
        text = re.sub(r"\s+", " ", text).strip()
    if limit and len(text) > limit:
        return text[: max(limit - 1, 0)].rstrip() + "…"
    return text


def excel_safe_value(value: Any, *, max_chars: int = EXCEL_CELL_CHAR_LIMIT) -> Any:
    """Return a value safe to pass to openpyxl cell assignment."""
    if value is None or isinstance(value, (int, float, bool)):
        return value
    text = sanitize_text(value)
    if max_chars and len(text) > max_chars:
        suffix = "\n[TRUNCATED FOR EXCEL CELL LIMIT]"
        text = text[: max(max_chars - len(suffix), 0)].rstrip() + suffix
    return text


def excel_safe_row(values: Sequence[Any], *, max_chars: int = EXCEL_CELL_CHAR_LIMIT) -> list[Any]:
    """Sanitize every cell in a row before appending to an XLSX worksheet."""
    return [excel_safe_value(v, max_chars=max_chars) for v in values]


def technical_error_details(exc: BaseException, *, limit: int = 4000) -> str:
    """Detailed but sanitized error text for collapsed UI/debug expanders."""
    detail = f"{exc.__class__.__name__}: {sanitize_text(str(exc))}"
    if len(detail) > limit:
        detail = detail[: max(limit - 1, 0)].rstrip() + "…"
    return detail


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def safe_filename(name: str | None, fallback: str = "file") -> str:
    """Return an ASCII-ish filename safe for temp storage and downloads."""
    base = os.path.basename(name or fallback)
    base = re.sub(r"[^A-Za-z0-9._ -]+", "_", base).strip(" ._")
    if not base:
        base = fallback
    if len(base) > 140:
        stem, ext = os.path.splitext(base)
        base = stem[:120].rstrip(" ._") + ext[:20]
    return base


def file_extension(name: str | None) -> str:
    return Path(name or "").suffix.lower().lstrip(".")


def get_project_dir(project_id: str) -> Path:
    return APP_TEMP_ROOT / safe_filename(project_id, fallback="project")


def ensure_project_workspace(session_state: Mapping[str, Any] | dict[str, Any]) -> tuple[str, dict[str, Path]]:
    """Ensure the active session has a project ID and isolated folders."""
    project_id = session_state.get("project_id")  # type: ignore[attr-defined]
    if not isinstance(project_id, str) or not project_id.strip():
        project_id = uuid.uuid4().hex
        session_state["project_id"] = project_id  # type: ignore[index]

    base = get_project_dir(project_id)
    paths = {"base": base}
    base.mkdir(parents=True, exist_ok=True)
    os.utime(base, None)
    for subdir in PROJECT_SUBDIRS:
        p = base / subdir
        p.mkdir(parents=True, exist_ok=True)
        paths[subdir] = p
    return project_id, paths


def cleanup_old_project_dirs(
    root: Path = APP_TEMP_ROOT,
    retention_hours: int = DEFAULT_RETENTION_HOURS,
    exclude_project_id: str | None = None,
) -> list[str]:
    """Delete stale project folders and return the deleted folder names."""
    root.mkdir(parents=True, exist_ok=True)
    cutoff = time.time() - max(retention_hours, 1) * 3600
    removed: list[str] = []
    for child in root.iterdir():
        if exclude_project_id and child.name == exclude_project_id:
            continue
        if not child.is_dir():
            continue
        try:
            if child.stat().st_mtime < cutoff:
                shutil.rmtree(child)
                removed.append(child.name)
        except OSError:
            # Cleanup is best-effort; do not break the app on filesystem races.
            continue
    return removed


def validate_uploaded_files(
    files: Sequence[tuple[str, int | None]],
    allowed_exts: Sequence[str],
    max_file_bytes: int = MAX_UPLOAD_BYTES,
    max_total_bytes: int = MAX_TOTAL_UPLOAD_BYTES,
) -> list[str]:
    """Validate extension and size for uploaded files."""
    errors: list[str] = []
    allowed = {e.lower().lstrip(".") for e in allowed_exts}
    total = 0
    for name, size in files:
        ext = file_extension(name)
        size = int(size or 0)
        total += size
        if ext not in allowed:
            allowed_list = ", ".join(f".{e}" for e in sorted(allowed))
            errors.append(f"{name}: unsupported file type .{ext or 'unknown'}; allowed types are {allowed_list}.")
        if size > max_file_bytes:
            errors.append(f"{name}: file is {size / (1024 * 1024):.1f} MB, above the {MAX_UPLOAD_MB} MB per-file limit.")
    if total > max_total_bytes:
        errors.append(f"Combined uploads are {total / (1024 * 1024):.1f} MB, above the {MAX_TOTAL_UPLOAD_MB} MB total limit.")
    return errors


def unique_path(directory: Path, filename: str) -> Path:
    """Return a non-colliding path inside directory."""
    directory.mkdir(parents=True, exist_ok=True)
    filename = safe_filename(filename)
    path = directory / filename
    if not path.exists():
        return path
    stem, ext = os.path.splitext(filename)
    for i in range(1, 10_000):
        candidate = directory / f"{stem}_{i}{ext}"
        if not candidate.exists():
            return candidate
    raise RuntimeError(f"Could not allocate a unique filename for {filename}.")


def write_bytes_unique(directory: Path, filename: str, data: bytes, prefix: str | None = None) -> Path:
    """Write bytes into directory using a safe, non-colliding filename."""
    safe = safe_filename(filename)
    if prefix:
        safe = f"{safe_filename(prefix)}_{safe}"
    path = unique_path(directory, safe)
    path.write_bytes(data)
    return path


def friendly_error_message(exc: BaseException) -> str:
    """Short user-facing error text without tracebacks or huge extracted text."""
    exc_name = exc.__class__.__name__
    if exc_name == "IllegalCharacterError":
        return (
            "One file contained hidden control characters that cannot be written to Excel. "
            "The app sanitizes extracted text where possible; use the collapsed details only if you need diagnostics."
        )
    msg = shorten_text(str(exc).strip() or exc_name, limit=280)
    return msg


def log_event(
    project_id: str,
    phase: str,
    event: str,
    success: bool,
    *,
    provider: str | None = None,
    model: str | None = None,
    metrics: Mapping[str, Any] | None = None,
    details: Mapping[str, Any] | None = None,
    error: str | None = None,
    log_dir: Path | None = None,
) -> Path | None:
    """Append a single structured JSON event to the project log.

    Do not pass full source text, prompts, generated survey text, or API keys in
    metrics/details. This log is intended for operational debugging only.
    """
    try:
        if log_dir is None:
            log_dir = get_project_dir(project_id) / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        payload: dict[str, Any] = {
            "timestamp_utc": _now_iso(),
            "project_id": project_id,
            "phase": phase,
            "event": event,
            "success": bool(success),
        }
        if provider:
            payload["provider"] = provider
        if model:
            payload["model"] = model
        if metrics:
            payload["metrics"] = dict(metrics)
        if details:
            payload["details"] = dict(details)
        if error:
            payload["error"] = error
        path = log_dir / "events.jsonl"
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=True, default=str) + "\n")
        return path
    except OSError:
        return None
