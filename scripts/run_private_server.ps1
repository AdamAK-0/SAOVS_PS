param(
    [string]$HostName = "0.0.0.0",
    [int]$HttpPort = 8000,
    [int]$HttpsPort = 8443,
    [string]$Python = "python",
    [string]$ContentRoot = "",
    [string]$AssetBase = "https://assets-os.saovs.channel.or.jp/",
    [string]$AssetHosts = "assets-os.saovs.channel.or.jp",
    [switch]$HttpOnly
)

$ErrorActionPreference = "Stop"

$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
$Runtime = Join-Path $Root "runtime"
$Logs = Join-Path $Runtime "logs"
$Cert = Join-Path $Root "certs\saovs_api.pem"
$Key = Join-Path $Root "certs\saovs_api.key"

New-Item -ItemType Directory -Force -Path $Logs | Out-Null

if ([string]::IsNullOrWhiteSpace($ContentRoot)) {
    $ContentRoot = Join-Path $Root "content\SAOVS\data1\com.bandainamcoent.saovsww\files"
}

function Assert-PortAvailable {
    param([int]$Port)

    $listener = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if ($listener) {
        $process = Get-CimInstance Win32_Process -Filter "ProcessId=$($listener.OwningProcess)" -ErrorAction SilentlyContinue
        $name = if ($process) { $process.Name } else { "unknown" }
        throw "Port $Port is already listening under PID $($listener.OwningProcess) ($name). Choose another port."
    }
}

Assert-PortAvailable -Port $HttpPort
if (-not $HttpOnly) {
    Assert-PortAvailable -Port $HttpsPort
}

$env:PYTHONPATH = Join-Path $Root "src"
$env:SAOVS_SERVER_ROOT = $Root
$env:SAOVS_DB = Join-Path $Runtime "saovs.sqlite3"
$env:SAOVS_LOG_DIR = $Logs
$env:SAOVS_CONTENT_ROOT = $ContentRoot
$env:SAOVS_ASSET_BASE = $AssetBase
$env:SAOVS_ASSET_HOSTS = $AssetHosts
$env:SAOVS_ASSET_VER = "30000"
$env:SAOVS_MASTER_DATA_VER = "30000"
$env:SAOVS_LOCALIZE_DATA_VER = "30000"

Write-Host "SAOVS private server"
Write-Host "  root:        $Root"
Write-Host "  content:     $ContentRoot"
Write-Host "  database:    $env:SAOVS_DB"
Write-Host "  asset base:  $AssetBase"
Write-Host "  asset hosts: $AssetHosts"

if (-not (Test-Path -LiteralPath $ContentRoot)) {
    Write-Warning "Content root does not exist yet: $ContentRoot"
}

if ($HttpOnly) {
    & $Python -m saovs_private_server.compat_server --host $HostName --port $HttpPort
    exit $LASTEXITCODE
}

if (-not (Test-Path -LiteralPath $Cert) -or -not (Test-Path -LiteralPath $Key)) {
    throw "HTTPS cert/key not found. Expected $Cert and $Key"
}

$httpOut = Join-Path $Logs "http_stdout.log"
$httpErr = Join-Path $Logs "http_stderr.log"
$httpsOut = Join-Path $Logs "https_stdout.log"
$httpsErr = Join-Path $Logs "https_stderr.log"

$http = Start-Process -FilePath $Python `
    -ArgumentList @("-m", "saovs_private_server.compat_server", "--host", $HostName, "--port", "$HttpPort") `
    -WorkingDirectory $Root `
    -RedirectStandardOutput $httpOut `
    -RedirectStandardError $httpErr `
    -WindowStyle Hidden `
    -PassThru

$https = Start-Process -FilePath $Python `
    -ArgumentList @("-m", "saovs_private_server.compat_server", "--host", $HostName, "--port", "$HttpsPort", "--ssl-cert", $Cert, "--ssl-key", $Key) `
    -WorkingDirectory $Root `
    -RedirectStandardOutput $httpsOut `
    -RedirectStandardError $httpsErr `
    -WindowStyle Hidden `
    -PassThru

Write-Host "  http pid:    $($http.Id) port $HttpPort"
Write-Host "  https pid:   $($https.Id) port $HttpsPort"
Write-Host "  health:      http://127.0.0.1:$HttpPort/admin/health"
