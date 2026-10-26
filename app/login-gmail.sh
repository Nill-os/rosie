#!/bin/bash
# board #738 quirk-testing-weekly-relogin
cd "$(dirname "$0")" && .venv/bin/python -c "import sys; sys.path.insert(0,'app'); import gmail; print('Connected:', gmail.login(sys.argv[1] if len(sys.argv)>1 else 'main'))" "$@"
