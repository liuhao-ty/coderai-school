$ErrorActionPreference = "Continue"
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$LogDir = Join-Path $ProjectRoot "workspace_data\logs"
$issues = [System.Collections.Generic.List[string]]::new()
$report = [System.Collections.Generic.List[string]]::new()

function Add-Check([string]$Name, [bool]$Passed, [string]$Detail) {
    $status = if ($Passed) { "OK" } else { "FAIL" }
    $line = "[$status] $Name - $Detail"
    $script:report.Add($line)
    Write-Host $line -ForegroundColor $(if ($Passed) { "Green" } else { "Red" })
    if (-not $Passed) { $script:issues.Add($Name) }
}

Set-Location $ProjectRoot
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
Add-Check "Python" ($null -ne (Get-Command python -ErrorAction SilentlyContinue)) $(python --version 2>&1)
Add-Check "Node.js" ($null -ne (Get-Command node -ErrorAction SilentlyContinue)) $(node --version 2>&1)
Add-Check "npm.cmd" ($null -ne (Get-Command npm.cmd -ErrorAction SilentlyContinue)) $(npm.cmd --version 2>&1)
Add-Check "Frontend dependencies" (Test-Path "node_modules\vite\bin\vite.js") "node_modules/vite"

python -c "import fastapi, sqlalchemy, uvicorn" 2>$null
Add-Check "Backend dependencies" ($LASTEXITCODE -eq 0) "FastAPI, SQLAlchemy, Uvicorn"
python -c "import sqlite3; c=sqlite3.connect(r'workspace_data/coderai.db'); print(c.execute('PRAGMA quick_check').fetchone()[0]); c.close()" 2>$null | Out-Null
Add-Check "SQLite integrity" ($LASTEXITCODE -eq 0) "workspace_data/coderai.db"

foreach ($service in @(
    @{ Name = "API"; Url = "http://127.0.0.1:8000/api/health"; Port = 8000; PidFile = ".codex-api.pid" },
    @{ Name = "Frontend"; Url = "http://127.0.0.1:5173"; Port = 5173; PidFile = ".codex-web.pid" }
)) {
    $httpOk = $false
    try { $httpOk = (Invoke-WebRequest $service.Url -UseBasicParsing -TimeoutSec 3).StatusCode -eq 200 } catch {}
    Add-Check "$($service.Name) HTTP" $httpOk $service.Url
    $listener = Get-NetTCPConnection -LocalPort $service.Port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
    $savedPid = if (Test-Path $service.PidFile) { (Get-Content $service.PidFile -Raw).Trim() } else { "missing" }
    $pidOk = $listener -and ([string]$listener.OwningProcess -eq $savedPid)
    Add-Check "$($service.Name) PID" $pidOk "file=$savedPid listener=$($listener.OwningProcess)"
}

$dataSize = (Get-ChildItem (Join-Path $ProjectRoot "workspace_data") -File -Recurse -ErrorAction SilentlyContinue | Measure-Object Length -Sum).Sum
$report.Add("[INFO] Workspace storage - $([math]::Round(($dataSize / 1MB), 2)) MB")
$reportPath = Join-Path $LogDir "diagnostic-latest.log"
$report | Set-Content -Encoding UTF8 $reportPath
Write-Host "Diagnostic report: $reportPath" -ForegroundColor Cyan
if ($issues.Count -gt 0) { exit 1 }
