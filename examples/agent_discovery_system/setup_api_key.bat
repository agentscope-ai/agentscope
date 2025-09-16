@echo off
REM Set Gemini API key for the current session
set GEMINI_API_KEY=AIzaSyAIjsJCDEtJ14pdxfRIS4uj9sjriB_ff6I

echo ✅ Gemini API key has been set for this session
echo 🔑 API Key: AIzaSyAIjsJCDEtJ14pdxfRIS4uj9sjriB_ff6I
echo.
echo 🚀 You can now run the Agent Discovery System:
echo    python run_discovery_system.py
echo.
echo 🌐 Or start the server directly:
echo    python discovery_server.py
echo.
echo ⚠️  Note: This API key is only set for the current PowerShell session.
echo    To make it permanent, add it to your system environment variables.
echo.

REM Keep the window open
pause