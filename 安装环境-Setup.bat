@echo off
rem  [aipdd fork delta] AIPDD DramaClaw one-click environment installer (Windows).
rem  Double-click this file. Everything else is in scripts\windows_setup.ps1.
rem
rem  Do NOT "Run as administrator": the installer uses Docker Desktop's per-user
rem  mode, which needs no elevation and lands in the CURRENT user's profile. An
rem  elevated window would install it into the admin account instead, and then the
rem  normal-user launcher would not find docker.exe. (WSL setup still raises its own
rem  system consent dialog in step 2 - that one jump is unavoidable and expected.)
rem
rem  This file is intentionally ASCII-only: cmd.exe parses batch files with the
rem  console codepage, so Chinese literals here would turn into mojibake. All
rem  human-readable text lives in the UTF-8-with-BOM PowerShell files.
setlocal enableextensions
chcp 65001 >nul
title AIPDD DramaClaw - Setup
cd /d "%~dp0"

set "PS=powershell.exe"
where powershell.exe >nul 2>&1
if errorlevel 1 set "PS=%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe"

if exist "%~dp0scripts\windows_setup.ps1" goto :run
echo.
echo [!] scripts\windows_setup.ps1 is missing next to this .bat.
echo     Keep this .bat in the repository root; do not move it out alone.
echo.
pause
goto :eof

:run
"%PS%" -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\windows_setup.ps1"
set "RC=%ERRORLEVEL%"
rem 0 = installed fine, window closes by itself (the requested auto-exit).
if "%RC%"=="0" goto :eof
rem >=3 = the script already printed its own report and waited for Enter.
if %RC% GEQ 3 goto :eof
echo.
echo [!] The installer stopped with code %RC% without printing its own report.
echo     Usual cause: an ExecutionPolicy pushed by Group Policy overrides the
echo     -ExecutionPolicy switch. Check with:  Get-ExecutionPolicy -List
echo     Otherwise screenshot this window and send it to the maintainer.
echo.
pause
