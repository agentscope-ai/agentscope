@echo off
chcp 65001 >nul
title 我的 Agent 服务

echo ============================================
echo   我的 Agent 服务一键启动
echo ============================================
echo.

REM 1. 启动 Redis
echo [1/3] 启动 Redis...
start "Redis-Server" /MIN "C:\googoe\Redis-x64-5.0.14.1\redis-server.exe"
timeout /t 2 /nobreak >nul
echo       Redis 已启动 (127.0.0.1:6379)
echo.

REM 2. 启动 Agent Service
echo [2/3] 启动 Agent Service...
cd /d "%~dp0my_agent_service"
start "My-Agent-Service-8000" cmd /c "uv run python main.py"
timeout /t 3 /nobreak >nul
echo       Agent Service 已启动 (http://localhost:8000)
echo.

REM 3. 启动 Web UI
echo [3/3] 启动 Web UI...
cd /d "%~dp0web_ui"

REM 安装依赖（首次需要）
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
echo   Web UI:     http://localhost:5173
echo   Agent API:  http://localhost:8000
echo   API 文档:   http://localhost:8000/docs
echo ============================================
echo.
echo   使用步骤:
echo   1. 打开 http://localhost:5173
echo   2. 设置 Server URL: http://localhost:8000
echo   3. 添加凭证 (DeepSeek API Key)
echo   4. 创建 Agent 开始对话
echo.
echo   关闭窗口即可停止服务（Redis 需手动关）
pause
