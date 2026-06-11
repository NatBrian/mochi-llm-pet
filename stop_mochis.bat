@echo off
:: Stop all running Mochi LLM Pet instances
echo Stopping all Mochi cats...
taskkill /F /FI "WINDOWTITLE eq Mochi - Ginger*" >nul 2>&1
taskkill /F /FI "WINDOWTITLE eq Mochi - Grey*" >nul 2>&1
taskkill /F /FI "WINDOWTITLE eq Mochi - Grey-White*" >nul 2>&1
echo All Mochi cats stopped.
timeout /t 3 /nobreak >nul