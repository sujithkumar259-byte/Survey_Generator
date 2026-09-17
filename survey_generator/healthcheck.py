"""Container health check for the Survey Generator app.

This intentionally avoids importing Streamlit or provider SDKs. It verifies that
core Phase 1/2 modules import and that the survey-outline workbook contract is
intact. Phase 3 (Word rendering) is handled by the separate Stage-3 R Shiny app
and is intentionally not checked here.
"""
from __future__ import annotations

import sys
from pathlib import Path

REQUIRED_FILES = [
    "app.py",
    "requirements.txt",
    "contract.py",
    "phase1/extract.py",
    "phase2/pipeline.py",
    "phase2/excel_writer.py",
]


def main() -> int:
    root = Path(__file__).resolve().parent
    sys.path.insert(0, str(root.parent))
    missing = [name for name in REQUIRED_FILES if not (root / name).exists()]
    if missing:
        print("missing required files: " + ", ".join(missing), file=sys.stderr)
        return 1
    try:
        from survey_generator.contract import SUPPORTED_QUESTION_TYPES, OUTLINE_COLS  # noqa: PLC0415
        from survey_generator.phase2.excel_writer import write_survey_spec  # noqa: F401,PLC0415
        if not SUPPORTED_QUESTION_TYPES or not OUTLINE_COLS:
            print("workbook contract is empty", file=sys.stderr)
            return 1
    except Exception as exc:  # noqa: BLE001
        print(f"healthcheck import failed: {exc}", file=sys.stderr)
        return 1
    print("ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
