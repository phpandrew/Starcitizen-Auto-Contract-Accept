@echo off
REM ====================================================================
REM  One-time setup: installs the Python libraries the watcher needs.
REM  Run this once before using run.bat.
REM
REM  You ALSO need Tesseract OCR installed separately:
REM    https://github.com/UB-Mannheim/tesseract/wiki
REM ====================================================================
color 0F
title Auto-Accept - Install Requirements
echo Installing Python libraries for the Star Citizen auto-accept watcher...
echo.
python -m pip install --upgrade pip
python -m pip install mss pillow pytesseract pydirectinput keyboard pystray
echo.
echo --------------------------------------------------------------------
echo If the lines above show "Successfully installed", you are good to go.
echo If you see "python is not recognized", install Python first from
echo https://www.python.org/downloads/  (tick "Add python.exe to PATH").
echo.
echo Remember: also install Tesseract OCR from
echo https://github.com/UB-Mannheim/tesseract/wiki
echo --------------------------------------------------------------------
pause
