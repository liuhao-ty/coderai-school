param(
    [Parameter(Mandatory = $true)]
    [string]$AssetBaseUrl,
    [string]$Notes = "CoderAI School beta update",
    [string]$BundleDirectory = "src-tauri/target/release/bundle/nsis",
    [string]$Version = ""
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$bundlePath = Join-Path $projectRoot $BundleDirectory
if (-not (Test-Path -LiteralPath $bundlePath -PathType Container)) {
    throw "Updater artifact directory does not exist: $bundlePath"
}
if (-not $AssetBaseUrl.StartsWith("https://")) {
    throw "The updater asset URL must use HTTPS."
}
if (-not $Version) {
    $package = Get-Content -LiteralPath (Join-Path $projectRoot "package.json") -Raw | ConvertFrom-Json
    $Version = [string]$package.version
}

$artifact = Get-ChildItem -LiteralPath $bundlePath -File | Where-Object {
    ($_.Name.EndsWith("-setup.exe") -or $_.Name.EndsWith(".nsis.zip")) -and
        (Test-Path -LiteralPath "$($_.FullName).sig")
} | Sort-Object @{ Expression = { if ($_.Name.EndsWith("-setup.exe")) { 0 } else { 1 } } }, Name | Select-Object -First 1
if (-not $artifact) {
    throw "No signed NSIS updater installer was found."
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
$json = $manifest | ConvertTo-Json -Depth 8
[IO.File]::WriteAllText($target, $json + [Environment]::NewLine, [Text.UTF8Encoding]::new($false))
Write-Output $target
