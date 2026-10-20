#!/bin/bash
# Sign in to Google (needed once, then again about every 7 days).
cd "$(dirname "$0")" && .venv/bin/python -c "import sys; sys.path.insert(0,'app'); import gcal; gcal.login(); print('Logged in.')"
