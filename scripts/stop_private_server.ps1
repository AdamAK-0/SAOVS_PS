$ErrorActionPreference = "Stop"

$servers = Get-CimInstance Win32_Process |
    Where-Object { $_.Name -eq "python.exe" -and $_.CommandLine -like "*saovs_private_server.compat_server*" }

foreach ($server in $servers) {
    Write-Host "Stopping SAOVS private server PID $($server.ProcessId)"
    Stop-Process -Id $server.ProcessId -Force -ErrorAction SilentlyContinue
}

if (-not $servers) {
    Write-Host "No SAOVS private server processes found."
}

