@echo off
echo ===================================================
echo   OmniHarness - Server Starter (0.0.0.0:8000)
echo ===================================================
echo.
python -m uvicorn harness.server:app --host 0.0.0.0 --port 8000 --reload
pause
