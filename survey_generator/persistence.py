"""File-backed project persistence, artifact versioning, and audit trail.

This lightweight store gives the Streamlit app durable project metadata without
requiring a database. Project records, artifacts, snapshots, status changes,
and audit events are written to per-project folders. A future database-backed
implementation can keep the same ProjectStore/FileProjectStore method names.
"""
from __future__ import annotations

import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from .runtime import APP_TEMP_ROOT, safe_filename

STORE_ROOT = Path(os.environ.get("SURVEY_GENERATOR_PROJECT_STORE", str(APP_TEMP_ROOT / "project_store")))
PROJECT_STATUSES = [
    "Draft",
    "Hypotheses Generated",
    "Hypotheses Approved",
    "Sections Proposed",
    "Sections Approved",
    "Questions Generated",
    "Needs Review",
    "Validated",
    "Exported",
    "Final",
]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _json_default(obj: Any) -> Any:
    if hasattr(obj, "model_dump"):
        return obj.model_dump()
    if isinstance(obj, Path):
        return str(obj)
    return str(obj)


def _read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return default


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=_json_default), encoding="utf-8")


def project_dir(project_id: str) -> Path:
    return STORE_ROOT / safe_filename(project_id, "project")


def ensure_project_record(project_id: str, *, owner: str = "anonymous", title: str = "Untitled Study") -> dict[str, Any]:
    store = FileProjectStore(STORE_ROOT)
    return store.ensure_project(project_id, owner=owner, metadata={"title": title})


def get_project_record(project_id: str) -> dict[str, Any]:
    return _read_json(project_dir(project_id) / "project.json", {})


def update_project_status(
    project_id: str,
    status: str,
    *,
    owner: str = "anonymous",
    title: str | None = None,
    data: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    store = FileProjectStore(STORE_ROOT)
    metadata = dict(data or {})
    if title:
        metadata["title"] = title
    return store.set_status(project_id, status, owner=owner, metadata=metadata)


def save_project_snapshot(project_id: str, name: str, payload: Any, *, owner: str = "anonymous") -> Path:
    pdir = project_dir(project_id) / "snapshots"
    pdir.mkdir(parents=True, exist_ok=True)
    path = pdir / f"{safe_filename(name, 'snapshot')}.json"
    _write_json(path, {"saved_at": _now(), "owner": owner or "anonymous", "payload": payload})
    audit_event(project_id, owner, "snapshot_saved", {"name": name, "path": str(path)})
    return path


def audit_event(project_id: str, actor: str, action: str, details: Mapping[str, Any] | None = None) -> Path | None:
    try:
        pdir = project_dir(project_id)
        pdir.mkdir(parents=True, exist_ok=True)
        payload = {
            "timestamp_utc": _now(),
            "project_id": project_id,
            "actor": actor or "anonymous",
            "action": action,
            "details": dict(details or {}),
        }
        path = pdir / "audit.jsonl"
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=True, default=_json_default) + "\n")
        return path
    except OSError:
        return None


def register_artifact(
    project_id: str,
    artifact_type: str,
    file_path: str | Path,
    *,
    owner: str = "anonymous",
    metadata: Mapping[str, Any] | None = None,
    version: int | None = None,
) -> dict[str, Any]:
    store = FileProjectStore(STORE_ROOT)
    final_version = store.record_artifact(project_id, artifact_type, file_path, version=version, metadata={"owner": owner, **dict(metadata or {})})
    rec = store.project_record(project_id)
    for item in reversed(rec.get("artifacts") or []):
        if item.get("artifact_type") == artifact_type and int(item.get("version", -1)) == final_version:
            return item
    return {"artifact_type": artifact_type, "version": final_version}


def list_artifacts(project_id: str) -> list[dict[str, Any]]:
    rec = get_project_record(project_id)
    return list(rec.get("artifacts") or [])


class FileProjectStore:
    """Small file-backed store used by app.py and tests."""

    statuses = PROJECT_STATUSES

    def __init__(self, root: str | Path | None = None):
        root_path = Path(root or STORE_ROOT)
        # If a sqlite-like filename is supplied, use a sibling folder so callers
        # can pass a future database path without breaking today's JSON store.
        if root_path.suffix in {".sqlite", ".sqlite3", ".db"}:
            self.db_path = str(root_path)
            root_path = root_path.with_suffix("")
        else:
            self.db_path = str(root_path)
        self.root = root_path
        self.root.mkdir(parents=True, exist_ok=True)

    def _project_dir(self, project_id: str) -> Path:
        return self.root / safe_filename(project_id, "project")

    def _record_path(self, project_id: str) -> Path:
        return self._project_dir(project_id) / "project.json"

    def _read_record(self, project_id: str) -> dict[str, Any]:
        return _read_json(self._record_path(project_id), {})

    def _write_record(self, project_id: str, rec: Mapping[str, Any]) -> None:
        _write_json(self._record_path(project_id), dict(rec))

    def ensure_project(
        self,
        project_id: str,
        *,
        owner: str = "anonymous",
        status: str = "Draft",
        metadata: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        metadata = dict(metadata or {})
        title = str(metadata.get("study_title") or metadata.get("title") or "Untitled Study")
        rec = self._read_record(project_id)
        if not rec:
            rec = {
                "project_id": project_id,
                "owner": owner or "anonymous",
                "title": title,
                "status": status if status in PROJECT_STATUSES else "Draft",
                "created_at": _now(),
                "updated_at": _now(),
                "data": {},
                "artifacts": [],
                "snapshot_versions": {},
            }
        rec["owner"] = owner or rec.get("owner", "anonymous")
        rec["title"] = title or rec.get("title", "Untitled Study")
        if status in PROJECT_STATUSES:
            rec["status"] = status
        rec.setdefault("data", {}).update(metadata)
        rec.setdefault("artifacts", [])
        rec.setdefault("snapshot_versions", {})
        rec["updated_at"] = _now()
        self._write_record(project_id, rec)
        return rec

    def project_record(self, project_id: str) -> dict[str, Any]:
        return self._read_record(project_id)

    def get_project(self, project_id: str) -> dict[str, Any]:
        return self.project_record(project_id)

    def set_status(self, project_id: str, status: str, *, owner: str = "anonymous", metadata: Mapping[str, Any] | None = None) -> dict[str, Any]:
        if status not in PROJECT_STATUSES:
            raise ValueError(f"Unknown project status {status!r}. Expected one of: {', '.join(PROJECT_STATUSES)}")
        rec = self.ensure_project(project_id, owner=owner, metadata=metadata)
        rec["status"] = status
        rec["updated_at"] = _now()
        self._write_record(project_id, rec)
        self.audit_event(project_id, owner, "project", "status_changed", True, details={"status": status})
        return rec

    def next_artifact_version(self, project_id: str, artifact_type: str) -> int:
        rec = self.ensure_project(project_id)
        versions = [int(a.get("version", 0)) for a in rec.get("artifacts", []) if a.get("artifact_type") == artifact_type]
        return max(versions or [0]) + 1

    def record_artifact(
        self,
        project_id: str,
        artifact_type: str,
        path: str | Path,
        *,
        version: int | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> int:
        src = Path(path)
        if not src.exists():
            raise FileNotFoundError(str(src))
        rec = self.ensure_project(project_id)
        version = int(version or self.next_artifact_version(project_id, artifact_type))
        dest_dir = self._project_dir(project_id) / "artifacts" / safe_filename(artifact_type, "artifact")
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / f"v{version:03d}_{safe_filename(src.name, 'artifact')}"
        if dest.exists():
            version = self.next_artifact_version(project_id, artifact_type)
            dest = dest_dir / f"v{version:03d}_{safe_filename(src.name, 'artifact')}"
        shutil.copy2(src, dest)
        item = {
            "artifact_type": artifact_type,
            "version": version,
            "filename": dest.name,
            "path": str(dest),
            "source_path": str(src),
            "created_at": _now(),
            "owner": str((metadata or {}).get("owner") or rec.get("owner") or "anonymous"),
            "metadata": dict(metadata or {}),
        }
        rec.setdefault("artifacts", []).append(item)
        rec["updated_at"] = _now()
        self._write_record(project_id, rec)
        self.audit_event(project_id, item["owner"], "artifact", "artifact_recorded", True, details={"artifact_type": artifact_type, "version": version})
        return version

    def save_snapshot(self, project_id: str, payload: Any, *, status: str = "Draft") -> int:
        rec = self.ensure_project(project_id)
        versions = rec.setdefault("snapshot_versions", {})
        version = int(versions.get(status, 0)) + 1
        versions[status] = version
        snap_dir = self._project_dir(project_id) / "snapshots"
        snap_dir.mkdir(parents=True, exist_ok=True)
        path = snap_dir / f"{safe_filename(status.lower(), 'snapshot')}_v{version:03d}.json"
        _write_json(path, {"saved_at": _now(), "status": status, "version": version, "payload": payload})
        rec["updated_at"] = _now()
        self._write_record(project_id, rec)
        self.audit_event(project_id, str(rec.get("owner", "anonymous")), "project", "snapshot_saved", True, details={"status": status, "version": version})
        return version

    def audit_event(self, project_id: str, actor: str, phase: str, event: str, success: bool, *, details: Mapping[str, Any] | None = None) -> Path:
        pdir = self._project_dir(project_id)
        pdir.mkdir(parents=True, exist_ok=True)
        payload = {
            "timestamp_utc": _now(),
            "project_id": project_id,
            "actor": actor or "anonymous",
            "phase": phase,
            "event": event,
            "success": bool(success),
            "details": dict(details or {}),
        }
        path = pdir / "audit.jsonl"
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False, default=_json_default) + "\n")
        return path

    def export_audit_jsonl(self, project_id: str) -> bytes:
        path = self._project_dir(project_id) / "audit.jsonl"
        return path.read_bytes() if path.exists() else b""

    def list_artifacts(self, project_id: str) -> list[dict[str, Any]]:
        return list(self.project_record(project_id).get("artifacts") or [])


ProjectStore = FileProjectStore
_STORE = FileProjectStore(STORE_ROOT)


def get_project_store() -> FileProjectStore:
    return _STORE
