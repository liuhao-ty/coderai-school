param(
    [Parameter(Mandatory = $true)]
    [string]$AssetBaseUrl,
    [string]$Notes = "CoderAI 学堂内测更新",
    [string]$BundleDirectory = "src-tauri/target/release/bundle/nsis",
    [string]$Version = ""
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$bundlePath = Join-Path $projectRoot $BundleDirectory
if (-not (Test-Path -LiteralPath $bundlePath -PathType Container)) {
    throw "更新产物目录不存在：$bundlePath"
}
if (-not $AssetBaseUrl.StartsWith("https://")) {
    throw "更新下载地址必须使用 HTTPS。"
}
if (-not $Version) {
    $package = Get-Content -LiteralPath (Join-Path $projectRoot "package.json") -Raw | ConvertFrom-Json
    $Version = [string]$package.version
}

$artifact = Get-ChildItem -LiteralPath $bundlePath -File | Where-Object {
    $_.Name.EndsWith(".nsis.zip") -and (Test-Path -LiteralPath "$($_.FullName).sig")
} | Select-Object -First 1
if (-not $artifact) {
    throw "未找到带 .sig 的 NSIS 更新压缩包。"
}

$signature = (Get-Content -LiteralPath "$($artifact.FullName).sig" -Raw).Trim()
$encodedName = [Uri]::EscapeDataString($artifact.Name).Replace("%2F", "/")
$manifest = [ordered]@{
    version = $Version
    notes = $Notes
    pub_date = [DateTimeOffset]::UtcNow.ToString("o")
    platforms = [ordered]@{
        "windows-x86_64" = [ordered]@{
            signature = $signature
            url = "$($AssetBaseUrl.TrimEnd('/'))/$encodedName"
        }
    }
}

$target = Join-Path $bundlePath "latest.json"
$manifest | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $target -Encoding utf8
Write-Output $target
