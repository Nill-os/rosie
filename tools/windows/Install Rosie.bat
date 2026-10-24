@echo off
rem Installs Rosie on this PC (or repairs an install). Needs the internet. Double-click it once.
title Installing Rosie
powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://raw.githubusercontent.com/Nill-os/rosie/main/tools/windows/install.ps1 | iex"
pause
