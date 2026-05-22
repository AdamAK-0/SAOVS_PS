param(
    [string]$HostName = "0.0.0.0",
    [int]$HttpPort = 80,
    [int]$HttpsPort = 443,
    [string]$Python = "python",
    [string]$ContentRoot = "",
    [string]$AssetBase = "https://assets-os-login-lab.saovs.com/",
    [string]$AssetHosts = "assets-os-login-lab.saovs.com,assets-os.saovs.channel.or.jp",
    [string]$AuthResultOrigin = "",
    [Alias("CertFile")]
    [string]$SslCert = "",
    [Alias("KeyFile")]
    [string]$SslKey = "",
    [string]$AssetVer = "30000",
    [string]$MasterDataVer = "202",
    [int]$LocalizeDataVer = 161,
    [int]$DefaultUserId = 183705490,
    [int64]$DefaultUserCode = 46841725594,
    [string]$Backend = "",
    [switch]$HttpOnly
)

$ErrorActionPreference = "Stop"

$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
$Runtime = Join-Path $Root "runtime"
$Logs = Join-Path $Runtime "logs"

function Get-DefaultLanIPv4 {
    $configs = Get-NetIPConfiguration -ErrorAction SilentlyContinue |
        Where-Object { $_.IPv4DefaultGateway -and $_.IPv4Address } |
        Sort-Object -Property InterfaceMetric

    foreach ($config in $configs) {
        foreach ($addr in $config.IPv4Address) {
            $ip = $addr.IPAddress
            if ($ip -and $ip -notlike "127.*" -and $ip -notlike "169.254.*") {
                return $ip
            }
        }
    }

    $addr = Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
        Where-Object { $_.IPAddress -notlike "127.*" -and $_.IPAddress -notlike "169.254.*" } |
        Select-Object -First 1
    return $addr.IPAddress
}

function Get-OriginFromUrl {
    param([string]$Url)

    try {
        $uri = [Uri]$Url
        if ($uri.Scheme -and $uri.Host) {
            if ($uri.IsDefaultPort) {
                return "$($uri.Scheme)://$($uri.Host)"
            }
            return "$($uri.Scheme)://$($uri.Host):$($uri.Port)"
        }
    } catch {
        return ""
    }

    return ""
}

function Resolve-SAOVSContentRoot {
    param([string]$Path)

    if ([string]::IsNullOrWhiteSpace($Path)) {
        return ""
    }

    $resolved = [System.IO.Path]::GetFullPath($Path)
    $candidates = @(
        $resolved,
        (Join-Path $resolved "data1\com.bandainamcoent.saovsww\files"),
        (Join-Path $resolved "data1\com.bandaicoent.saovswww\files"),
        (Join-Path $resolved "files")
    )

    foreach ($candidate in $candidates) {
        if (Test-Path -LiteralPath (Join-Path $candidate "sword.db")) {
            return $candidate
        }
    }

    return $resolved
}

if ([string]::IsNullOrWhiteSpace($AuthResultOrigin)) {
    $AuthResultOrigin = Get-OriginFromUrl -Url $AssetBase
    if ([string]::IsNullOrWhiteSpace($AuthResultOrigin)) {
        $lanIp = Get-DefaultLanIPv4
        if ($lanIp) {
            $AuthResultOrigin = "http://$lanIp"
        } else {
            $AuthResultOrigin = "http://127.0.0.1"
        }
    }
}

if ([string]::IsNullOrWhiteSpace($SslCert)) {
    $SslCert = Join-Path $Root "certs\public-saovs\fullchain.pem"
    if (-not (Test-Path -LiteralPath $SslCert)) {
        $SslCert = Join-Path $Root "certs\saovs_api.pem"
        if (-not (Test-Path -LiteralPath $SslCert)) {
            $SslCert = Join-Path $Root "certs\saovs-local-combined\saovs-local-combined.pem"
        }
    }
}

if ([string]::IsNullOrWhiteSpace($SslKey)) {
    $SslKey = Join-Path $Root "certs\public-saovs\privkey.pem"
    if (-not (Test-Path -LiteralPath $SslKey)) {
        $SslKey = Join-Path $Root "certs\saovs_api.key"
        if (-not (Test-Path -LiteralPath $SslKey)) {
            $SslKey = Join-Path $Root "certs\saovs-local-combined\saovs-local-combined.key"
        }
    }
}

New-Item -ItemType Directory -Force -Path $Logs | Out-Null

if ([string]::IsNullOrWhiteSpace($ContentRoot)) {
    $contentCandidates = @()
    if (-not [string]::IsNullOrWhiteSpace($env:SAOVS_CONTENT_ROOT)) {
        $contentCandidates += $env:SAOVS_CONTENT_ROOT
    }
    $contentCandidates += @(
        (Join-Path $Root "content\files"),
        (Join-Path $Root "content\SAOVS\data1\com.bandainamcoent.saovsww\files"),
        (Join-Path $Root "..\SAOVS_Project\SAOVS\data1\com.bandainamcoent.saovsww\files"),
        (Join-Path $Root "..\SAOVS_Project\SAOVS\data1\com.bandaicoent.saovswww\files")
    )

    foreach ($candidate in $contentCandidates) {
        if (-not [string]::IsNullOrWhiteSpace($candidate)) {
            $resolvedCandidate = [System.IO.Path]::GetFullPath($candidate)
            if (Test-Path -LiteralPath (Join-Path $resolvedCandidate "sword.db")) {
                $ContentRoot = $resolvedCandidate
                break
            }
        }
    }

    if ([string]::IsNullOrWhiteSpace($ContentRoot)) {
        $ContentRoot = Join-Path $Root "content\files"
    }
} else {
    $ContentRoot = Resolve-SAOVSContentRoot -Path $ContentRoot
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
$env:SAOVS_AUTH_RESULT_ORIGIN = $AuthResultOrigin
$env:SAOVS_RELATIVE_AUTH_RESULT_ORIGIN = $AuthResultOrigin
$env:SAOVS_ASSET_VER = $AssetVer
$env:SAOVS_MASTER_DATA_VER = $MasterDataVer
$env:SAOVS_LOCALIZE_DATA_VER = "$LocalizeDataVer"
$env:SAOVS_DEFAULT_USER_ID = "$DefaultUserId"
$env:SAOVS_DEFAULT_USER_CODE = "$DefaultUserCode"
if ([string]::IsNullOrWhiteSpace($Backend)) {
    if ([string]::IsNullOrWhiteSpace($env:SAOVS_SERVER_BACKEND)) {
        $env:SAOVS_SERVER_BACKEND = "cheroot"
    }
} else {
    $env:SAOVS_SERVER_BACKEND = $Backend
}
if ([string]::IsNullOrWhiteSpace($env:SAOVS_PUBLIC_API_BASE)) {
    $env:SAOVS_PUBLIC_API_BASE = "https://api-os-login-lab.saovs.com/"
}
if ([string]::IsNullOrWhiteSpace($env:SAOVS_PUBLIC_LOGIN_BASE)) {
    $env:SAOVS_PUBLIC_LOGIN_BASE = $AuthResultOrigin
}

Write-Host "SAOVS private server"
Write-Host "  root:        $Root"
Write-Host "  content:     $ContentRoot"
Write-Host "  database:    $env:SAOVS_DB"
Write-Host "  asset base:  $AssetBase"
Write-Host "  asset hosts: $AssetHosts"
Write-Host "  auth result: $AuthResultOrigin"
Write-Host "  public api:  $env:SAOVS_PUBLIC_API_BASE"
Write-Host "  public web:  $env:SAOVS_PUBLIC_LOGIN_BASE"
Write-Host "  backend:     $env:SAOVS_SERVER_BACKEND"
Write-Host "  versions:    asset=$AssetVer master=$MasterDataVer localize=$LocalizeDataVer"
Write-Host "  user:        id=$DefaultUserId code=$DefaultUserCode"

if (-not (Test-Path -LiteralPath $ContentRoot)) {
    Write-Warning "Content root does not exist yet: $ContentRoot"
}

if ($HttpOnly) {
    & $Python -m saovs_private_server.compat_server --host $HostName --port $HttpPort
    exit $LASTEXITCODE
}

if (-not (Test-Path -LiteralPath $SslCert) -or -not (Test-Path -LiteralPath $SslKey)) {
    throw "HTTPS cert/key not found. Expected $SslCert and $SslKey"
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
    -ArgumentList @("-m", "saovs_private_server.compat_server", "--host", $HostName, "--port", "$HttpsPort", "--ssl-cert", $SslCert, "--ssl-key", $SslKey) `
    -WorkingDirectory $Root `
    -RedirectStandardOutput $httpsOut `
    -RedirectStandardError $httpsErr `
    -WindowStyle Hidden `
    -PassThru

Write-Host "  http pid:    $($http.Id) port $HttpPort"
Write-Host "  https pid:   $($https.Id) port $HttpsPort"
Write-Host "  health:      http://127.0.0.1:$HttpPort/admin/health"
