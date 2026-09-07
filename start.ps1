# Launches the three local services: the Python ML sidecar, the Go gateway, and the Vite dev
# server. See docs/01-ARCHITECTURE.md section 2 for which component owns what.
#
#   Python sidecar  127.0.0.1:8801   preprocess, VAD, features, detector, speaker verification
#   Go gateway      127.0.0.1:8000   API, fusion, session, decision, alerts, audit
#   Vite            127.0.0.1:5173   dashboard, proxies /api and /ws to the gateway
$ErrorActionPreference = 'Stop'
$projectRoot = $PSScriptRoot

# Load .env if the operator has one. Keys reach the child processes through the environment,
# never through a command line, so they do not appear in the process list. See .env.example.
$envFile = Join-Path $projectRoot '.env'
if (Test-Path -LiteralPath $envFile) {
    foreach ($line in Get-Content -LiteralPath $envFile) {
        $trimmed = $line.Trim()
        if (-not $trimmed -or $trimmed.StartsWith('#')) { continue }
        $split = $trimmed.IndexOf('=')
        if ($split -lt 1) { continue }
        $name = $trimmed.Substring(0, $split).Trim()
        $value = $trimmed.Substring($split + 1).Trim()
        if ($value) { Set-Item -Path "Env:$name" -Value $value }
    }
    Write-Host 'Loaded .env'
}

$pythonPath = Join-Path $projectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonPath)) {
    $pythonPath = (Get-Command python -ErrorAction SilentlyContinue).Source
}
if (-not $pythonPath) {
    throw 'Install Python dependencies first. See README.md.'
}
$goPath = (Get-Command go -ErrorAction SilentlyContinue).Source
if (-not $goPath) {
    $candidate = 'C:\Program Files\Go\bin\go.exe'
    if (Test-Path -LiteralPath $candidate) { $goPath = $candidate }
}
if (-not $goPath) {
    throw 'Go is not installed or not on PATH. Install it with: winget install GoLang.Go'
}
if (-not (Test-Path -LiteralPath (Join-Path $projectRoot 'frontend\node_modules'))) {
    throw 'Run npm ci in frontend first. See README.md.'
}
foreach ($port in @(8000, 8801, 5173)) {
    if (Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue) {
        throw "Port $port is in use. Stop the existing service before running this launcher."
    }
}

& $pythonPath (Join-Path $projectRoot 'backend\generate_fixtures.py')

# Build the gateway up front so a compile error surfaces here rather than as a dead port.
$gatewayDir = Join-Path $projectRoot 'gateway'
$gatewayExe = Join-Path $gatewayDir 'voxguard.exe'
Write-Host 'Building the Go gateway...'
& $goPath build -C $gatewayDir -o voxguard.exe ./cmd/voxguard
if ($LASTEXITCODE -ne 0) { throw 'Gateway build failed.' }

$sidecarProcess = $null
$gatewayProcess = $null
$frontendProcess = $null
try {
    $sidecarProcess = Start-Process -FilePath $pythonPath `
        -ArgumentList '-m', 'app.sidecar' `
        -WorkingDirectory (Join-Path $projectRoot 'backend') -WindowStyle Hidden -PassThru

    # The gateway tolerates a sidecar that is not up yet — it reports degraded rather than
    # failing — but starting it second keeps the first health check honest.
    Start-Sleep -Seconds 2

    $gatewayProcess = Start-Process -FilePath $gatewayExe `
        -WorkingDirectory $projectRoot -WindowStyle Hidden -PassThru

    $frontendProcess = Start-Process -FilePath 'node' `
        -ArgumentList 'node_modules/vite/bin/vite.js', '--host', '127.0.0.1' `
        -WorkingDirectory (Join-Path $projectRoot 'frontend') -WindowStyle Hidden -PassThru

    Write-Host 'VoiceShield AI: http://127.0.0.1:5173'
    Write-Host 'Gateway health: http://127.0.0.1:8000/api/v1/health'
    Write-Host 'Press Enter to stop all three local services.'
    Read-Host | Out-Null
} finally {
    foreach ($process in @($frontendProcess, $gatewayProcess, $sidecarProcess)) {
        if ($process -and -not $process.HasExited) { Stop-Process -Id $process.Id -Force }
    }
}
