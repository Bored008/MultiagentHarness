@echo off
echo ===================================================
echo   SYNAPSE - Seed Initial Demo Graph
echo ===================================================
echo.
python scripts\seed_demo.py --server http://127.0.0.1:8000
echo.
pause
