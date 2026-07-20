$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$DataRoot = Join-Path $ProjectRoot "workspace_data"
$LogDir = Join-Path $DataRoot "logs"
$ExportDir = Join-Path $DataRoot "exports"
$StageDir = Join-Path $DataRoot ("diagnostic-export-" + [guid]::NewGuid().ToString("N"))
$timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
$zipPath = Join-Path $ExportDir "CoderAI-logs-$timestamp.zip"

New-Item -ItemType Directory -Force -Path $LogDir, $ExportDir, $StageDir | Out-Null
try {
    & (Join-Path $ProjectRoot "diagnose-coderai.ps1") *> (Join-Path $LogDir "diagnostic-export.log")
    Copy-Item -Path (Join-Path $LogDir "*.log") -Destination $StageDir -Force -ErrorAction SilentlyContinue
    $systemInfo = @(
        "Exported: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss zzz')",
        "OS: $([Environment]::OSVersion.VersionString)",
        "Python: $(python --version 2>&1)",
        "Node: $(node --version 2>&1)",
        "Project: $ProjectRoot"
    )
    $systemInfo | Set-Content -Encoding UTF8 (Join-Path $StageDir "system-info.txt")
    Compress-Archive -Path (Join-Path $StageDir "*") -DestinationPath $zipPath -CompressionLevel Optimal
} finally {
    $resolvedData = (Resolve-Path $DataRoot).Path
    $resolvedStage = (Resolve-Path $StageDir -ErrorAction SilentlyContinue).Path
    if ($resolvedStage -and $resolvedStage.IndexOf($resolvedData, [StringComparison]::OrdinalIgnoreCase) -eq 0) {
        Remove-Item -LiteralPath $resolvedStage -Recurse -Force
    }
}
Write-Host "Logs exported: $zipPath" -ForegroundColor Green
