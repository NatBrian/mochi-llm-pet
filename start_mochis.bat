@echo off
:: ============================================================
::  Start 3 Mochi LLM Pets (Ginger, Grey, Grey-White)
::  Double-click this file to launch all three cats.
::  Each cat has its own color, memory DB, and personality.
:: ============================================================

set PROJECT=C:\Users\\mochi-llm-pet

echo Launching 3 Mochi cats...

:: --- Ginger Cat ---
cscript //nologo "%PROJECT%\start_ginger.vbs"
echo   [1/3] Ginger cat launched.

:: Small delay so they don't all hit Ollama at the exact same moment
timeout /t 3 /nobreak >nul

:: --- Grey Cat ---
cscript //nologo "%PROJECT%\start_grey.vbs"
echo   [2/3] Grey cat launched.

timeout /t 3 /nobreak >nul

:: --- Grey-White Cat ---
cscript //nologo "%PROJECT%\start_grey_white.vbs"
echo   [3/3] Grey-White cat launched.

echo.
echo ========================================
echo   3 Mochi cats are alive!
echo   Ginger, Grey, and Grey-White
echo ========================================
echo.
echo To stop all cats, run:  stop_mochis.bat
echo.
timeout /t 5 /nobreak >nul