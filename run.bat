@echo off
REM ====================================================================
REM  Launches the Star Citizen Contract Share auto-accept watcher.
REM  Right-click this file and pick "Run as administrator" so the
REM  keypress reliably reaches the game.
REM ====================================================================
color 0F
title Star Citizen Auto-Accept
cd /d "%~dp0"
python sc_auto_accept.py
pause
