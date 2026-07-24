#!/bin/bash
# Double-click this file (macOS) to open the Agent Lab in your browser.
cd "$(dirname "$0")" || exit 1

PY=${PYTHON:-python3}
command -v "$PY" >/dev/null 2>&1 || PY=python
command -v "$PY" >/dev/null 2>&1 || { echo "No Python found. Install Python 3, then try again."; read -r; exit 1; }

if ! "$PY" -c "import requests, pptx, openpyxl" >/dev/null 2>&1; then
  echo "Installing the agent's Python packages (one time)..."
  "$PY" -m pip install --quiet requests python-pptx openpyxl || {
    echo; echo "Install failed. Run:  $PY -m pip install requests python-pptx openpyxl"; read -r; exit 1; }
fi

if ! curl -s -m 2 http://127.0.0.1:11434/api/tags >/dev/null 2>&1; then
  echo "Starting Ollama..."
  (ollama serve >/dev/null 2>&1 &) 2>/dev/null
  sleep 2
fi

exec "$PY" -m webui.server
