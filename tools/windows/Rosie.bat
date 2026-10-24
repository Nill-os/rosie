@echo off
rem Starts Rosie. It lives in %LOCALAPPDATA%\Rosie and keeps itself up to date (launcher.py).
title Rosie
set "R=%LOCALAPPDATA%\Rosie"
set "UV_PYTHON_INSTALL_DIR=%R%\tools\python"
set "UV_CACHE_DIR=%R%\tools\uv-cache"
"%R%\tools\uv.exe" run --no-project --python 3.12 --managed-python "%R%\launcher.py" %*
if errorlevel 1 pause
