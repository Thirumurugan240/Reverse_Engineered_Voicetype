@echo off
REM Developer mode: run VoiceType from source with a console for logs
cd /d "%~dp0"
".venv\Scripts\python.exe" app\main.py %*
