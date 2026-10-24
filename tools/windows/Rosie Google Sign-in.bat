@echo off
rem Signs in to Google Calendar and Gmail for Rosie (a browser window opens for each). Run it again when Rosie says the Google login expired.
title Rosie Google Sign-in
set "R=%LOCALAPPDATA%\Rosie"
set "UV_PYTHON_INSTALL_DIR=%R%\tools\python"
set "UV_CACHE_DIR=%R%\tools\uv-cache"
"%R%\tools\uv.exe" run --no-project --python 3.12 --managed-python "%R%\launcher.py" --google-login
pause
