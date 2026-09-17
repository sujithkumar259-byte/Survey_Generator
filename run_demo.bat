@echo off
REM ── Local demo launcher for the Survey Generator (Phases 1–2) ──
REM Double-click this file, or run it from a terminal.
cd /d "%~dp0"
echo Starting Survey Generator on http://localhost:8501 ...
".venv\Scripts\python.exe" -m streamlit run survey_generator\app.py
pause
