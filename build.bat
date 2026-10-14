@echo off
REM Build dist\VoiceType\VoiceType.exe (run setup.bat first)
cd /d "%~dp0"
".venv\Scripts\pip.exe" install -q pyinstaller || (pause & exit /b 1)

set SP=.venv\Lib\site-packages
set GPU=
if exist "%SP%\nvidia\cublas\bin\cublas64_12.dll" set GPU=--add-binary "%SP%\nvidia\cublas\bin\cublas64_12.dll;nvidia\cublas\bin" --add-binary "%SP%\nvidia\cublas\bin\cublasLt64_12.dll;nvidia\cublas\bin" --add-binary "%SP%\nvidia\cudnn\bin\*.dll;nvidia\cudnn\bin" --add-binary "%SP%\nvidia\cuda_nvrtc\bin\*.dll;nvidia\cuda_nvrtc\bin"

".venv\Scripts\pyinstaller.exe" --noconfirm --clean --windowed --onedir --name VoiceType ^
 --icon app\assets\icon.ico --paths app ^
 --add-data "app\ui;ui" --add-data "app\assets;assets" %GPU% ^
 --collect-all faster_whisper --collect-all ctranslate2 --collect-all webview ^
 --collect-all pystray --collect-data sounddevice --collect-binaries _sounddevice_data ^
 --hidden-import clr --hidden-import pythonnet --collect-all clr_loader ^
 app\main.py || (pause & exit /b 1)

echo.
echo Built: dist\VoiceType\VoiceType.exe
pause
