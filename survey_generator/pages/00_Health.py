import os
import time
from pathlib import Path

import streamlit as st

st.set_page_config(page_title="Survey Generator Health", page_icon="✓")

root = Path(os.environ.get("SURVEY_GENERATOR_DATA_ROOT", str(Path.cwd() / "data")))
temp_root = Path(os.environ.get("SURVEY_GENERATOR_TEMP_ROOT", "/tmp/survey_generator"))

checks = []
for name, path in (("data_root", root), ("temp_root", temp_root)):
    try:
        path.mkdir(parents=True, exist_ok=True)
        test = path / ".healthcheck"
        test.write_text(str(time.time()), encoding="utf-8")
        test.unlink(missing_ok=True)
        checks.append({"check": name, "status": "ok", "path": str(path)})
    except Exception as exc:  # noqa: BLE001
        checks.append({"check": name, "status": "error", "path": str(path), "detail": str(exc)})

healthy = all(row["status"] == "ok" for row in checks)
st.title("Health")
(st.success if healthy else st.error)("ok" if healthy else "error")
st.dataframe(checks, use_container_width=True, hide_index=True)
