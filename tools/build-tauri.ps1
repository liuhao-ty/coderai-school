param(
    [string]$ApiUrl = $env:CODERAI_PRODUCTION_API_URL,
    [string]$UpdaterEndpoint = $env:CODERAI_UPDATER_ENDPOINT,
    [string]$OrganizationCode = $(if ($env:CODERAI_ORGANIZATION_CODE) { $env:CODERAI_ORGANIZATION_CODE } else { "coderai-pilot" }),
    [string]$SecretRoot = $env:CODERAI_SECRET_ROOT,
    [switch]$UnsignedUpdater
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
if (-not $SecretRoot) {
    $SecretRoot = "$projectRoot-secrets"
}
$privateKeyPath = Join-Path $SecretRoot "updater-private.key"
$passwordPath = Join-Path $SecretRoot "updater-password.dpapi"

if (-not $ApiUrl -or -not [Uri]::IsWellFormedUriString($ApiUrl, [UriKind]::Absolute) -or -not $ApiUrl.StartsWith("https://")) {
    throw "Provide a production HTTPS API URL with -ApiUrl or CODERAI_PRODUCTION_API_URL."
}
if (-not $UpdaterEndpoint) {
    throw "Provide a production HTTPS updater URL with -UpdaterEndpoint or CODERAI_UPDATER_ENDPOINT."
}
$updaterValidationUrl = $UpdaterEndpoint.Replace("{{target}}", "windows").Replace("{{arch}}", "x86_64").Replace("{{current_version}}", "0.0.0")
if (-not [Uri]::IsWellFormedUriString($updaterValidationUrl, [UriKind]::Absolute) -or -not $UpdaterEndpoint.StartsWith("https://")) {
    throw "Provide a production HTTPS updater URL with -UpdaterEndpoint or CODERAI_UPDATER_ENDPOINT."
}

$apiOrigin = ([Uri]$ApiUrl).GetLeftPart([UriPartial]::Authority)
$updateOrigin = ([Uri]$updaterValidationUrl).GetLeftPart([UriPartial]::Authority)
$csp = "default-src 'self'; base-uri 'self'; object-src 'none'; frame-ancestors 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; font-src 'self' data:; connect-src 'self' ipc: http://ipc.localhost $apiOrigin $updateOrigin; img-src 'self' asset: http://asset.localhost data: blob: $apiOrigin; media-src 'self' asset: http://asset.localhost blob: $apiOrigin; frame-src 'self' blob: $apiOrigin"
$configOverrideData = @{
    app = @{ security = @{ csp = $csp } }
    plugins = @{ updater = @{ endpoints = @($UpdaterEndpoint) } }
}
if ($UnsignedUpdater) {
    $configOverrideData.bundle = @{ createUpdaterArtifacts = $false }
    $configOverrideData.plugins.updater = @{ endpoints = @() }
}
$configOverride = $configOverrideData | ConvertTo-Json -Depth 8 -Compress

$env:VITE_API_URL = $ApiUrl.TrimEnd("/")
$env:VITE_ORGANIZATION_CODE = $OrganizationCode.Trim().ToLowerInvariant()
$env:VITE_UPDATER_ENABLED = $(if ($UnsignedUpdater) { "false" } else { "true" })
$env:TAURI_CONFIG = $configOverride
$env:RUSTUP_HOME = "E:\Developer\Rust\rustup"
$env:CARGO_HOME = "E:\Developer\Rust\cargo"
$env:PATH = "E:\Developer\Rust\cargo\bin;$env:PATH"

if (-not $UnsignedUpdater) {
    if (-not (Test-Path -LiteralPath $privateKeyPath) -or -not (Test-Path -LiteralPath $passwordPath)) {
        throw "Updater signing material was not found under $SecretRoot."
    }
    $securePassword = (Get-Content -LiteralPath $passwordPath -Raw).Trim() | ConvertTo-SecureString
    $credential = [PSCredential]::new("updater", $securePassword)
    $env:TAURI_SIGNING_PRIVATE_KEY = $privateKeyPath
    $env:TAURI_SIGNING_PRIVATE_KEY_PASSWORD = $credential.GetNetworkCredential().Password
}

try {
    Push-Location $projectRoot
    npm.cmd run tauri:build
}
finally {
    Pop-Location
    Remove-Item Env:TAURI_SIGNING_PRIVATE_KEY -ErrorAction SilentlyContinue
    Remove-Item Env:TAURI_SIGNING_PRIVATE_KEY_PASSWORD -ErrorAction SilentlyContinue
}
