@echo off
REM One-time setup: creates a virtual environment and installs VoiceType's dependencies.
cd /d "%~dp0"
where python >nul 2>nul || (echo Python 3.10+ is required: https://www.python.org/downloads/ & pause & exit /b 1)

echo Creating virtual environment...
python -m venv .venv || (pause & exit /b 1)
".venv\Scripts\python.exe" -m pip install --upgrade pip
".venv\Scripts\pip.exe" install -r requirements.txt || (pause & exit /b 1)

where nvidia-smi >nul 2>nul && (
  echo NVIDIA GPU detected - installing CUDA libraries for fast transcription...
  ".venv\Scripts\pip.exe" install -r requirements-gpu.txt
)

echo.
echo Setup complete. Start VoiceType with run.bat, or run build.bat to create VoiceType.exe.
pause
