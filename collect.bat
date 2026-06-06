@echo off
REM ============================================================
REM APEX Auto Collector — runs every 4 hours via Task Scheduler
REM ============================================================

set PYTHON=C:\Users\jonat\AppData\Local\Programs\Python\Python311\python.exe
set DIR=c:\Users\jonat\Downloads\CryptoAgent\CryptoAgent

cd /d "%DIR%"

echo [%DATE% %TIME%] Starting APEX collection... >> logs\scheduler.log

%PYTHON% main.py --collect-phase8 >> logs\scheduler.log 2>&1
%PYTHON% main.py --collect-onchain >> logs\scheduler.log 2>&1

echo [%DATE% %TIME%] Collection done. >> logs\scheduler.log
echo. >> logs\scheduler.log
