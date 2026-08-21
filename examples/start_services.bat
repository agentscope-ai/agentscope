@echo off
chcp 65001 >nul
title AgentScope 全服务启动

echo ============================================
echo   AgentScope 三套服务一键启动
echo ============================================
echo.

REM 1. 启动 Redis
echo [1/3] 启动 Redis...
start "Redis-Server" /MIN "C:\googoe\Redis-x64-5.0.14.1\redis-server.exe"
timeout /t 2 /nobreak >nul
echo       Redis 已启动 (127.0.0.1:6379)
echo.

REM 2. 启动 Agent Service (Python)
echo [2/3] 启动 Agent Service...
cd /d "%~dp0agent_service"
start "Agent-Service-8000" cmd /c "uv run python main.py"
echo       Agent Service 已启动 (http://localhost:8000)
echo.

REM 3. 启动 Web UI 后端
echo [3/3] 启动 Web UI 后端 + 前端...
cd /d "%~dp0web_ui"

REM 安装依赖（首次需要，后续会跳过）
call pnpm install >nul 2>&1

REM 启动后端 (Express :3000)
start "WebUI-Backend-3000" cmd /c "cd /d %~dp0web_ui\backend && npx nodemon --watch src --ext ts --exec npx ts-node src/index.ts"
timeout /t 2 /nobreak >nul
echo       WebUI 后端已启动 (http://localhost:3000)

REM 启动前端 (Vite :5173)
start "WebUI-Frontend-5173" cmd /c "cd /d %~dp0web_ui\frontend && npx vite --host 0.0.0.0"
timeout /t 2 /nobreak >nul
echo       WebUI 前端已启动 (http://localhost:5173)

echo.
echo ============================================
echo   全部启动完成！
echo.
echo   前端界面: http://localhost:5173
echo   Agent API: http://localhost:8000
echo   WebUI 后端: http://localhost:3000
echo ============================================
echo.
echo   关闭窗口即可停止所有服务（Redis 需手动关）
pause
