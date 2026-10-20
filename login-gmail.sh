#!/bin/bash
# Connect (or reconnect) a Gmail account, READ-ONLY. Usage: ./login-gmail.sh [name]   (default name: main)
# Needed once per account, then about weekly while the Google app is in "Testing" mode.
cd "$(dirname "$0")" && .venv/bin/python -c "import sys; sys.path.insert(0,'app'); import gmail; print('Connected:', gmail.login(sys.argv[1] if len(sys.argv)>1 else 'main'))" "$@"
