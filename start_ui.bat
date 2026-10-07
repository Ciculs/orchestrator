@echo off
title AI Company Orchestrator
cd /d "E:\Work\Orchestrator"
set PYTHONIOENCODING=utf-8
echo ========================================================
echo       AI COMPANY ORCHESTRATOR - VIRTUAL ORG HQ
echo ========================================================
echo Starting Orchestrator UI & opening browser...
echo Local Gateway: http://127.0.0.1:20128 (9Router)
echo UI Dashboard:  http://127.0.0.1:20129
echo ========================================================
echo.
python -m ma.cli ui --port 20129
pause
