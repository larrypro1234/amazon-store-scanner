#!/bin/bash
# Double-click to open Amazon Store Scanner. Keep this window open while you use it.
cd "$(dirname "$0")" || exit 1

if [ ! -d .venv ]; then
  echo "First time setup (takes a couple of minutes)..."
  python3 -m venv .venv || { echo "Python 3 is missing. Install it from python.org"; read -r; exit 1; }
fi
source .venv/bin/activate
pip install -q --disable-pip-version-check -r requirements.txt
python -m playwright install chromium >/dev/null

python app.py
