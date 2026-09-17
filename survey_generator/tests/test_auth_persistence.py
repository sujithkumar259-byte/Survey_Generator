from __future__ import annotations

import uuid

from survey_generator.auth import AuthConfig, verify_password, verify_token
from survey_generator.persistence import PROJECT_STATUSES, ProjectStore


def test_auth_password_and_token_verification():
    import hashlib
    cfg = AuthConfig(
        enabled=True,
        users={"alice": hashlib.sha256(b"secret").hexdigest()},
        shared_token_hash=hashlib.sha256(b"token").hexdigest(),
    )
    assert verify_password("alice", "secret", cfg)
    assert not verify_password("alice", "wrong", cfg)
    assert verify_token("token", cfg)


def test_project_store_status_artifacts_and_audit(tmp_path):
    store = ProjectStore(tmp_path / "projects.sqlite3")
    pid = f"project-test-{uuid.uuid4().hex}"
    store.ensure_project(pid, owner="alice", metadata={"client_name": "Client", "study_title": "Study"})
    store.set_status(pid, "Hypotheses Generated", owner="alice")
    assert store.project_record(pid)["status"] == "Hypotheses Generated"
    artifact = tmp_path / "artifact.txt"
    artifact.write_text("hello", encoding="utf-8")
    v1 = store.record_artifact(pid, "test_artifact", artifact)
    v2 = store.record_artifact(pid, "test_artifact", artifact)
    assert (v1, v2) == (1, 2)
    snap_version = store.save_snapshot(pid, {"status": "ok"}, status="Draft")
    assert snap_version == 1
    store.audit_event(pid, "alice", "phase", "action", True, details={"x": 1})
    audit = store.export_audit_jsonl(pid).decode("utf-8")
    assert "action" in audit
    assert "Final" in PROJECT_STATUSES
