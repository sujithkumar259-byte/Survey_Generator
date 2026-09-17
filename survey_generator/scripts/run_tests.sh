#!/usr/bin/env bash
set -euo pipefail
python -m compileall -q .
python healthcheck.py
pytest -q
