param(
    [switch]$NoBrowser
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$ApiUrl = "http://127.0.0.1:8000/api/health"
$WebUrl = "http://127.0.0.1:5173"
$ApiPidFile = Join-Path $ProjectRoot ".codex-api.pid"
$WebPidFile = Join-Path $ProjectRoot ".codex-web.pid"
$LogDir = Join-Path $ProjectRoot "workspace_data\logs"

Set-Location $ProjectRoot
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

function Test-CommandAvailable([string]$Name) {
    return $null -ne (Get-Command $Name -ErrorAction SilentlyContinue)
}

function Test-Http([string]$Url) {
    try {
        $response = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 2
        return $response.StatusCode -ge 200 -and $response.StatusCode -lt 500
    } catch {
        return $false
    }
}

function Wait-ForHttp([string]$Url, [string]$Name, [int]$Attempts = 30) {
    for ($index = 0; $index -lt $Attempts; $index++) {
        if (Test-Http $Url) {
            Write-Host "[OK] $Name is ready." -ForegroundColor Green
            return
        }
        Start-Sleep -Milliseconds 500
    }
    throw "$Name startup timed out. Run npm.cmd run dev manually to inspect the error."
}

function Sync-PidFileFromPort([int]$Port, [string]$PidFile) {
    $connection = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($connection) {
        $connection.OwningProcess | Set-Content -Encoding ASCII $PidFile
    }
}

if (-not (Test-CommandAvailable "python")) {
    throw "Python was not found. Install Python 3.11 or newer and add it to PATH."
}
if (-not (Test-CommandAvailable "npm.cmd")) {
    throw "npm.cmd was not found. Install Node.js LTS and add it to PATH."
}
if (-not (Test-Path (Join-Path $ProjectRoot "node_modules"))) {
    Write-Host "Installing frontend dependencies..." -ForegroundColor Cyan
    & npm.cmd install
    if ($LASTEXITCODE -ne 0) { throw "Frontend dependency installation failed." }
}

python -c "import fastapi, sqlalchemy, uvicorn" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "Installing backend dependencies..." -ForegroundColor Cyan
    python -m pip install -r requirements.txt
    if ($LASTEXITCODE -ne 0) { throw "Backend dependency installation failed." }
}

if (Test-Http $ApiUrl) {
    Sync-PidFileFromPort 8000 $ApiPidFile
    Write-Host "[OK] API is already running." -ForegroundColor Green
} else {
    Write-Host "Starting API..." -ForegroundColor Cyan
    $apiProcess = Start-Process -FilePath "python" `
        -ArgumentList @("-m", "uvicorn", "backend.app.main:app", "--host", "127.0.0.1", "--port", "8000") `
        -WorkingDirectory $ProjectRoot -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput (Join-Path $LogDir "api.stdout.log") `
        -RedirectStandardError (Join-Path $LogDir "api.stderr.log")
    $apiProcess.Id | Set-Content -Encoding ASCII $ApiPidFile
    Wait-ForHttp $ApiUrl "API"
}

if (Test-Http $WebUrl) {
    Sync-PidFileFromPort 5173 $WebPidFile
    Write-Host "[OK] Frontend is already running." -ForegroundColor Green
} else {
    Write-Host "Starting frontend..." -ForegroundColor Cyan
    $viteEntry = Join-Path $ProjectRoot "node_modules\vite\bin\vite.js"
    $webProcess = Start-Process -FilePath (Get-Command node).Source `
        -ArgumentList @($viteEntry, "--host", "127.0.0.1", "--port", "5173") `
        -WorkingDirectory $ProjectRoot -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput (Join-Path $LogDir "web.stdout.log") `
        -RedirectStandardError (Join-Path $LogDir "web.stderr.log")
    $webProcess.Id | Set-Content -Encoding ASCII $WebPidFile
    Wait-ForHttp $WebUrl "Frontend"
}

Write-Host "CoderAI Classroom: $WebUrl" -ForegroundColor Yellow
if (-not $NoBrowser) {
    Start-Process $WebUrl
}
