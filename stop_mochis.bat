@echo off
:: Stop all running Mochi LLM Pet instances
echo Stopping all Mochi cats...
powershell -ExecutionPolicy Bypass -File "%~dp0stop_mochis.ps1"