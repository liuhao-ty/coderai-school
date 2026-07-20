param(
    [switch]$Quiet
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$targets = @(
    @{ Name = "API"; PidFile = Join-Path $ProjectRoot ".codex-api.pid"; Marker = "backend.app.main:app"; Port = 8000 },
    @{ Name = "Frontend"; PidFile = Join-Path $ProjectRoot ".codex-web.pid"; Marker = "vite"; Port = 5173 }
)

foreach ($target in $targets) {
    if (-not (Test-Path $target.PidFile)) {
        if (-not $Quiet) { Write-Host "[SKIP] $($target.Name) PID file does not exist." -ForegroundColor Yellow }
        continue
    }
    $pidValue = 0
    if (-not [int]::TryParse((Get-Content $target.PidFile -Raw).Trim(), [ref]$pidValue)) {
        Write-Warning "$($target.Name) PID file is invalid; it was not used."
        continue
    }
    $processInfo = Get-CimInstance Win32_Process -Filter "ProcessId = $pidValue" -ErrorAction SilentlyContinue
    if (-not $processInfo) {
        Remove-Item -LiteralPath $target.PidFile -Force
        if (-not $Quiet) { Write-Host "[OK] $($target.Name) was already stopped." -ForegroundColor Green }
        continue
    }
    $commandLine = [string]$processInfo.CommandLine
    $ownsPort = Get-NetTCPConnection -LocalPort $target.Port -State Listen -ErrorAction SilentlyContinue |
        Where-Object { $_.OwningProcess -eq $pidValue } | Select-Object -First 1
    if (-not $ownsPort -or $commandLine.IndexOf($target.Marker, [StringComparison]::OrdinalIgnoreCase) -lt 0) {
        Write-Warning "$($target.Name) PID $pidValue does not belong to this project; it was not stopped."
        continue
    }
    Stop-Process -Id $pidValue -Force
    Wait-Process -Id $pidValue -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $target.PidFile -Force
    if (-not $Quiet) { Write-Host "[OK] $($target.Name) stopped (PID $pidValue)." -ForegroundColor Green }
}
