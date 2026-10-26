#!/bin/bash
# board #806 quirk-google-login-expires
cd "$(dirname "$0")" && .venv/bin/python -c "import sys; sys.path.insert(0,'app'); import gcal; gcal.login(); print('Logged in.')"
