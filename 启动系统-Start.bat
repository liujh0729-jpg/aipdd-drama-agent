@echo off
rem  [aipdd fork delta] AIPDD DramaClaw one-click launcher (Windows).
rem  Double-click this file: it starts the stack, waits until the site really
rem  answers, opens the browser, then closes this window. Containers keep running
rem  in the background - closing the window does not stop them. Running it again
rem  while already up is harmless (compose up -d is idempotent).
rem
rem  Developer extra:  Start.bat --rebuild   (rebuild images before starting)
rem    rebuilds api/web images before starting. The flag is translated into the
rem    AIPDD_REBUILD environment variable rather than passed to PowerShell, because
rem    PowerShell would try to bind "--rebuild" as a parameter name.
rem
rem  ASCII-only on purpose (see the note in Setup.bat).
setlocal enableextensions
chcp 65001 >nul
title AIPDD DramaClaw - Start
cd /d "%~dp0"

set "PS=powershell.exe"
where powershell.exe >nul 2>&1
if errorlevel 1 set "PS=%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe"

set "AIPDD_REBUILD="
if /i "%~1"=="--rebuild" set "AIPDD_REBUILD=1"
if not "%~1"=="" if /i not "%~1"=="--rebuild" echo [!] ignoring unknown argument: %~1 - only --rebuild is supported

if exist "%~dp0scripts\windows_start.ps1" goto :run
echo.
echo [!] scripts\windows_start.ps1 is missing next to this .bat.
echo     Keep this .bat in the repository root; do not move it out alone.
echo     If the environment is not installed yet, run Setup.bat first.
echo.
pause
goto :eof

:run
"%PS%" -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\windows_start.ps1"
set "RC=%ERRORLEVEL%"
rem 0 = site is up and the browser was opened; close the window (containers stay).
if "%RC%"=="0" goto :eof
rem >=3 = the script already printed its own report and waited for Enter.
if %RC% GEQ 3 goto :eof
echo.
echo [!] The launcher stopped with code %RC% without printing its own report.
echo     Known cause 1: an ExecutionPolicy pushed by Group Policy overrides the
echo     -ExecutionPolicy switch. Check with:  Get-ExecutionPolicy -List
echo     Known cause 2: an unhandled PowerShell error (a red record above this
echo     line). Observed on a fresh machine: a native command writing to stderr
echo     aborts the whole script under ErrorActionPreference=Stop.
echo     Either way: screenshot this window, red record included, and send it in.
echo     Otherwise screenshot this window and send it to the maintainer.
echo.
pause
