# Set Gemini API key for the current session
$env:GEMINI_API_KEY = "AIzaSyAIjsJCDEtJ14pdxfRIS4uj9sjriB_ff6I"

Write-Host "✅ Gemini API key has been set for this session" -ForegroundColor Green
Write-Host "🔑 API Key: AIzaSyAIjsJCDEtJ14pdxfRIS4uj9sjriB_ff6I" -ForegroundColor Cyan
Write-Host ""
Write-Host "🚀 You can now run the Agent Discovery System:" -ForegroundColor Yellow
Write-Host "   python run_discovery_system.py" -ForegroundColor White
Write-Host ""
Write-Host "🌐 Or start the server directly:" -ForegroundColor Yellow
Write-Host "   python discovery_server.py" -ForegroundColor White
Write-Host ""
Write-Host "⚠️  Note: This API key is only set for the current PowerShell session." -ForegroundColor Magenta
Write-Host "   To make it permanent, add it to your system environment variables." -ForegroundColor Magenta
Write-Host ""

# Test the API key
Write-Host "🧪 Testing API key..." -ForegroundColor Yellow
python test_gemini.py