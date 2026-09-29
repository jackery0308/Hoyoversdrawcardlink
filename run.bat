@echo off
REM Double-click to open the interactive menu, or pass args: run.bat genshin watch --validate
cd /d "%~dp0"
py -3 gacha_link.py %* 2>nul || python gacha_link.py %*
