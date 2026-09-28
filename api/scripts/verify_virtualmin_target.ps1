#Requires -Version 5.1
<#
.SYNOPSIS
    Verify a Virtualmin-hosted SDS target without deploying content.
.DESCRIPTION
    Checks the remote document root via SSH, probes the public URL with curl,
    and optionally tails Virtualmin/PHP logs. The script does not copy files,
    change ownership, alter Apache, or store secrets.
.PARAMETER PublicUrl
    Public HTTPS URL to probe, for example https://sds.example.com/.
.PARAMETER SshHost
    SSH host or IP address.
.PARAMETER SshUser
    SSH user.
.PARAMETER SshPort
    SSH port. Defaults to 22.
.PARAMETER IdentityFile
    SSH private-key path.
.PARAMETER DocRoot
    Virtualmin document root to list.
.PARAMETER AccessLog
    Optional access log path to tail.
.PARAMETER ErrorLog
    Optional error log path to tail.
.PARAMETER PhpLog
    Optional PHP log path to tail.
.PARAMETER SkipCurl
    Skip public URL probes.
.PARAMETER SkipRemote
    Skip SSH probes.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$PublicUrl,
    [Parameter(Mandatory = $true)][string]$SshHost,
    [Parameter(Mandatory = $true)][string]$SshUser,
    [int]$SshPort = 22,
    [Parameter(Mandatory = $true)][string]$IdentityFile,
    [Parameter(Mandatory = $true)][string]$DocRoot,
    [string]$AccessLog,
    [string]$ErrorLog,
    [string]$PhpLog,
    [switch]$SkipCurl,
    [switch]$SkipRemote
)

$ErrorActionPreference = "Stop"

function Write-Step {
    param([Parameter(Mandatory = $true)][string]$Message)
    Write-Host ""
    Write-Host "==> $Message" -ForegroundColor Cyan
}

function Invoke-CheckedCommand {
    param(
        [Parameter(Mandatory = $true)][string]$Command,
        [Parameter(Mandatory = $true)][string[]]$Arguments
    )
    & $Command @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "$Command failed with exit code $LASTEXITCODE"
    }
}

function Invoke-Remote {
    param([Parameter(Mandatory = $true)][string]$RemoteCommand)
    $target = "${SshUser}@${SshHost}"
    Invoke-CheckedCommand -Command "ssh" -Arguments @(
        "-i",
        $IdentityFile,
        "-p",
        [string]$SshPort,
        $target,
        $RemoteCommand
    )
}

if (-not $SkipRemote) {
    Write-Step "Remote document root"
    Invoke-Remote -RemoteCommand "sudo -n ls -la $DocRoot"

    $logs = @(
        @{ Label = "access log"; Path = $AccessLog },
        @{ Label = "error log"; Path = $ErrorLog },
        @{ Label = "PHP log"; Path = $PhpLog }
    )
    foreach ($log in $logs) {
        if (-not $log.Path) { continue }
        Write-Step "Remote $($log.Label)"
        Invoke-Remote -RemoteCommand "sudo -n tail -n 80 $($log.Path) 2>/dev/null || true"
    }
}

if (-not $SkipCurl) {
    Write-Step "Public URL headers"
    Invoke-CheckedCommand -Command "curl.exe" -Arguments @("-I", $PublicUrl)

    Write-Step "Public URL body head"
    $body = & curl.exe -sS $PublicUrl
    if ($LASTEXITCODE -ne 0) {
        throw "curl.exe failed with exit code $LASTEXITCODE"
    }
    $body | Select-Object -First 10
}

Write-Host ""
Write-Host "Verification completed." -ForegroundColor Green
