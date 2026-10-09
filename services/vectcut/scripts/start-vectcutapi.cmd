@echo off
title VectCutAPI Server
echo Starting VectCutAPI Backend on http://127.0.0.1:9001 ...
cd /d C:\Dev\capcut\VectCutAPI
C:\Dev\capcut\VectCutAPI\.venv\Scripts\python.exe capcut_server.py
pause
