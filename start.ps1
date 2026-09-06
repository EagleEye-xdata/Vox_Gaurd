$ErrorActionPreference = 'Stop'
$projectRoot = $PSScriptRoot
$pythonPath = Join-Path $projectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonPath)) {
    throw 'Install dependencies first. See README.md.'
}
if (-not (Test-Path -LiteralPath (Join-Path $projectRoot 'frontend\node_modules'))) {
    throw 'Run npm ci in frontend first. See README.md.'
}
foreach ($port in @(8000, 5173)) {
    if (Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue) {
        throw "Port $port is in use. Stop the existing service before running this launcher."
    }
}
& $pythonPath (Join-Path $projectRoot 'backend\generate_fixtures.py')
$backendProcess = Start-Process -FilePath $pythonPath -ArgumentList '-m','uvicorn','app.main:app','--host','127.0.0.1','--port','8000' -WorkingDirectory (Join-Path $projectRoot 'backend') -WindowStyle Hidden -PassThru
$frontendProcess = $null
try {
    $frontendProcess = Start-Process -FilePath 'node' -ArgumentList 'node_modules/vite/bin/vite.js','--host','127.0.0.1' -WorkingDirectory (Join-Path $projectRoot 'frontend') -WindowStyle Hidden -PassThru
    Write-Host 'VoiceShield AI: http://127.0.0.1:5173'
    Write-Host 'Press Enter to stop both local services.'
    Read-Host | Out-Null
} finally {
    if ($frontendProcess -and -not $frontendProcess.HasExited) { Stop-Process -Id $frontendProcess.Id }
    if (-not $backendProcess.HasExited) { Stop-Process -Id $backendProcess.Id }
}
