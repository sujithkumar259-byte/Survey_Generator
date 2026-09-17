@echo off
REM ============================================================
REM  Launch the Survey Generator (all phases, one Python app).
REM    http://localhost:8501   <-- open this in your browser
REM
REM  Phase 3 (the Word .docx renderer) is now built into this
REM  Python/Streamlit app — no separate R Shiny server needed.
REM ============================================================
cd /d "%~dp0"

echo Launching Survey Generator on http://localhost:8501 ...
start "Survey Generator - http://localhost:8501" cmd /k "%~dp0run_demo.bat"

echo.
echo A window is opening. Give it ~20 seconds to start, then open
echo   http://localhost:8501  in your browser.
echo This launcher window can be closed.
timeout /t 6 >nul
