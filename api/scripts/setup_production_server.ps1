#Requires -Version 5.1
<#
.SYNOPSIS
    Optional SDS self-host reference setup.
.DESCRIPTION
    Uses the historical compose.production.yml and .env.production files as an
    optional Docker Compose reference profile. Public SDS API users may install
    dependencies and operate PostgreSQL however they choose; this helper is not
    the required deployment model. It generates strong missing secrets without
    printing them, starts PostgreSQL + API, and verifies core API readiness.
.PARAMETER AllowedOrigin
    One or more production origins, for example https://sds.example.com.
.PARAMETER AllowLocalhostOrigin
    Permit localhost/127.0.0.1 in ALLOWED_ORIGINS. Intended only for local
    verification of the production profile.
.PARAMETER ApiPort
    Host port for the API. Defaults to 8090.
.PARAMETER PostgresPort
    Host port for PostgreSQL. Defaults to 5432.
.PARAMETER ProjectName
    Optional Docker Compose project name. Use this for disposable drills that
    must not touch an existing sds-api-prod stack.
.PARAMETER EnvFile
    Optional production environment file. Relative paths resolve from api/.
.PARAMETER ResetDatabase
    Destructive reset of the production compose database volume.
.PARAMETER NoBuild
    Start without building the API image.
.PARAMETER Rebuild
    Rebuild the API image with no cache before starting.
.PARAMETER SkipVerify
    Skip post-startup verification.
#>
param(
    [string[]]$AllowedOrigin,
    [switch]$AllowLocalhostOrigin,
    [int]$ApiPort = 8090,
    [int]$PostgresPort = 5432,
    [string]$ProjectName,
    [string]$EnvFile,
    [switch]$ResetDatabase,
    [switch]$NoBuild,
    [switch]$Rebuild,
    [switch]$SkipVerify
)

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ApiRoot = (Resolve-Path (Join-Path $ScriptDir "..")).Path
$DefaultEnvFile = Join-Path $ApiRoot ".env.production"
if (-not $EnvFile) {
    $EnvFile = $DefaultEnvFile
}
elseif (-not [System.IO.Path]::IsPathRooted($EnvFile)) {
    $EnvFile = Join-Path $ApiRoot $EnvFile
}
$EnvExample = Join-Path $ApiRoot ".env.example"
$ComposeFile = Join-Path $ApiRoot "compose.production.yml"
$ArtifactsDir = Join-Path $ApiRoot "artifacts\setup-production-server"
$ApiBaseUrl = "http://127.0.0.1:$ApiPort"

$script:TranscriptStarted = $false
$script:EnvWasCreated = $false

if (-not (Test-Path $ArtifactsDir)) {
    New-Item -ItemType Directory -Path $ArtifactsDir -Force | Out-Null
}
$Timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
$LogFile = Join-Path $ArtifactsDir "setup-$Timestamp.log"
Start-Transcript -Path $LogFile -Force | Out-Null
$script:TranscriptStarted = $true

function Stop-SetupTranscript {
    if ($script:TranscriptStarted) {
        try { Stop-Transcript | Out-Null } catch {}
        $script:TranscriptStarted = $false
    }
}

function Write-Step {
    param([Parameter(Mandatory = $true)][string]$Message)
    Write-Host "[$(Get-Date -Format "HH:mm:ss")] $Message"
}

function Write-Ok {
    param([Parameter(Mandatory = $true)][string]$Message)
    Write-Host "  OK: $Message" -ForegroundColor Green
}

function Write-Warn {
    param([Parameter(Mandatory = $true)][string]$Message)
    Write-Host "  WARN: $Message" -ForegroundColor Yellow
}

function Fail-Setup {
    param(
        [Parameter(Mandatory = $true)][string]$Message,
        [string]$Remediation = "Run .\scripts\dev.ps1 ProductionLogs from api, then retry after fixing the reported issue."
    )
    Write-Host ""
    Write-Host "ERROR: $Message" -ForegroundColor Red
    if ($Remediation) {
        Write-Host "Fix: $Remediation" -ForegroundColor Cyan
    }
    Write-Host "Log file: $LogFile" -ForegroundColor DarkGray
    Write-Host ""
    Stop-SetupTranscript
    exit 1
}

function Ensure-Command {
    param([Parameter(Mandatory = $true)][string]$Command, [Parameter(Mandatory = $true)][string]$Name)
    Write-Step "Checking $Name ..."
    if (-not (Get-Command $Command -ErrorAction SilentlyContinue)) {
        Fail-Setup "$Name is not installed or is not on PATH." "Install Docker Desktop or Docker Engine with the compose plugin."
    }
    Write-Ok "$Name found"
}

function Ensure-DockerCompose {
    Write-Step "Checking Docker Compose ..."
    try {
        $null = & docker compose version 2>&1
        if ($LASTEXITCODE -ne 0) { throw "docker compose version failed" }
    }
    catch {
        Fail-Setup "Docker Compose is not available." "Install Docker Compose v2 and retry."
    }
    Write-Ok "Docker Compose found"
}

function Ensure-DockerEngine {
    Write-Step "Checking Docker Engine ..."
    try {
        $null = & docker info 2>&1
        if ($LASTEXITCODE -ne 0) { throw "docker info failed" }
    }
    catch {
        Fail-Setup "Docker Engine is not running." "Start Docker and wait until the engine is ready."
    }
    Write-Ok "Docker Engine is running"
}

function Ensure-PathExists {
    param([Parameter(Mandatory = $true)][string]$Path, [Parameter(Mandatory = $true)][string]$Name)
    Write-Step "Checking $Name ..."
    if (-not (Test-Path $Path)) {
        Fail-Setup "Required path not found: $Path" "Ensure the repository checkout includes $Name."
    }
    Write-Ok "$Name found"
}

function Get-DotEnvValue {
    param([Parameter(Mandatory = $true)][string]$Key)
    if (-not (Test-Path $EnvFile)) { return $null }
    foreach ($line in Get-Content -LiteralPath $EnvFile) {
        if ($line -match "^\s*#") { continue }
        if ($line -match "^\s*$([regex]::Escape($Key))\s*=(.*)$") {
            $value = $matches[1].Trim()
            if ($value.StartsWith('"') -and $value.EndsWith('"')) {
                return $value.Substring(1, $value.Length - 2)
            }
            return $value
        }
    }
    return $null
}

function Set-DotEnvValue {
    param([Parameter(Mandatory = $true)][string]$Key, [Parameter(Mandatory = $true)][string]$Value)
    $lines = @()
    $found = $false
    if (Test-Path $EnvFile) {
        foreach ($line in Get-Content -LiteralPath $EnvFile) {
            if ($line -match "^\s*$([regex]::Escape($Key))\s*=.*$") {
                $lines += "$Key=$Value"
                $found = $true
            }
            else {
                $lines += $line
            }
        }
    }
    if (-not $found) {
        $lines += "$Key=$Value"
    }
    $utf8NoBom = New-Object -TypeName System.Text.UTF8Encoding -ArgumentList $false
    [System.IO.File]::WriteAllLines($EnvFile, [string[]]$lines, $utf8NoBom)
}

function Test-PlaceholderValue {
    param([AllowNull()][string]$Value)
    if (-not $Value) { return $true }
    return ($Value -match '^change_me($|_)')
}

function New-SafeSecret {
    param([int]$Length = 32)
    $chars = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
    $bytes = New-Object byte[] $Length
    $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try {
        $rng.GetBytes($bytes)
        return -join ($bytes | ForEach-Object { $chars[[int]($_ % $chars.Length)] })
    }
    finally {
        $rng.Dispose()
    }
}

function ConvertTo-OriginJson {
    param([Parameter(Mandatory = $true)][string[]]$Origins)
    $quoted = foreach ($origin in $Origins) {
        '"' + (($origin.Trim() -replace '\\', '\\') -replace '"', '\"') + '"'
    }
    return "[" + ($quoted -join ",") + "]"
}

function Test-OriginIsLocalOrWildcard {
    param([AllowNull()][string]$OriginsText)
    if (-not $OriginsText) { return $true }
    return ($OriginsText -match '\*' -or $OriginsText -match 'localhost' -or $OriginsText -match '127\.0\.0\.1')
}

function Invoke-Compose {
    param([Parameter(Mandatory = $true)][string[]]$Arguments, [switch]$Capture)
    Push-Location $ApiRoot
    try {
        $composeArgs = @("compose")
        if ($ProjectName) {
            $composeArgs += @("-p", $ProjectName)
        }
        $composeArgs += @("--env-file", $EnvFile, "-f", $ComposeFile)
        $composeArgs += $Arguments
        if ($Capture) {
            $output = & docker @composeArgs 2>&1
            if ($LASTEXITCODE -ne 0) { throw "docker compose failed: $($Arguments -join ' ')" }
            return $output
        }
        & docker @composeArgs
        if ($LASTEXITCODE -ne 0) { throw "docker compose failed: $($Arguments -join ' ')" }
    }
    finally {
        Pop-Location
    }
}

function Get-ComposePs {
    $raw = Invoke-Compose -Arguments @("ps", "--format", "json") -Capture
    $text = ($raw | Out-String).Trim()
    if (-not $text) { return @() }
    try {
        return @($text | ConvertFrom-Json)
    }
    catch {
        $items = @()
        foreach ($line in ($text -split "`r?`n")) {
            if ($line.Trim()) { $items += ($line | ConvertFrom-Json) }
        }
        return @($items)
    }
}

function Test-TcpPortFree {
    param([Parameter(Mandatory = $true)][int]$Port)
    $listener = $null
    try {
        $listener = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Any, $Port)
        $listener.Start()
        return $true
    }
    catch {
        return $false
    }
    finally {
        if ($listener) { $listener.Stop() }
    }
}

function Test-PortOwnedByCompose {
    param([Parameter(Mandatory = $true)][int]$Port)
    try {
        foreach ($service in (Get-ComposePs)) {
            foreach ($publisher in @($service.Publishers)) {
                if ($null -ne $publisher.PublishedPort -and [int]$publisher.PublishedPort -eq $Port) {
                    return $true
                }
            }
        }
    }
    catch {}
    return $false
}

function Ensure-Port {
    param([Parameter(Mandatory = $true)][int]$Port, [Parameter(Mandatory = $true)][string]$Name)
    Write-Step "Checking $Name port $Port ..."
    if (Test-TcpPortFree -Port $Port) {
        Write-Ok "$Name port $Port is available"
        return
    }
    if (Test-PortOwnedByCompose -Port $Port) {
        Write-Ok "$Name port $Port is already owned by this production compose project"
        return
    }
    Fail-Setup "$Name port $Port is occupied by another process." "Stop the process or rerun with a different -ApiPort/-PostgresPort."
}

function Get-ComposeProjectName {
    if ($ProjectName) {
        return $ProjectName
    }
    $nameLine = Select-String -LiteralPath $ComposeFile -Pattern "^\s*name:\s*(\S+)\s*$" -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($nameLine -and $nameLine.Matches.Count -gt 0) {
        return $nameLine.Matches[0].Groups[1].Value.Trim('"').Trim("'")
    }
    return (Split-Path -Leaf $ApiRoot).ToLowerInvariant()
}

function Remove-ComposeDatabaseVolume {
    $projectName = Get-ComposeProjectName
    $volumeIds = & docker volume ls --quiet --filter "label=com.docker.compose.project=$projectName" --filter "label=com.docker.compose.volume=postgres_data" 2>$null
    if ($LASTEXITCODE -ne 0) {
        Fail-Setup "Could not inspect Docker volumes for project $projectName." "Check Docker, then retry."
    }
    if (-not $volumeIds) {
        Write-Warn "No production database volume found for project $projectName"
        return
    }
    foreach ($volumeId in @($volumeIds)) {
        if ($volumeId) {
            & docker volume rm $volumeId | Out-Null
            if ($LASTEXITCODE -ne 0) {
                Fail-Setup "Could not remove database volume $volumeId." "Stop containers using it, then retry -ResetDatabase."
            }
            Write-Ok "Removed database volume $volumeId"
        }
    }
}

function Ensure-ProductionEnv {
    Write-Step "Preparing production environment file $EnvFile ..."
    if (-not (Test-Path $EnvFile)) {
        if (-not (Test-Path $EnvExample)) {
            Fail-Setup ".env.production is missing and .env.example was not found." "Create .env.production with the required production values."
        }
        $envParent = Split-Path -Parent $EnvFile
        if ($envParent -and -not (Test-Path $envParent)) {
            New-Item -ItemType Directory -Path $envParent -Force | Out-Null
        }
        Copy-Item -LiteralPath $EnvExample -Destination $EnvFile
        $script:EnvWasCreated = $true
        Write-Warn "Created $EnvFile from .env.example"
    }

    $secretKeys = @(
        @{ Key = "JWT_SECRET_KEY"; Length = 64 },
        @{ Key = "POSTGRES_PASSWORD"; Length = 32 },
        @{ Key = "BOOTSTRAP_ADMIN_PASSWORD"; Length = 32 },
        @{ Key = "EXPORT_SIGNING_SECRET"; Length = 64 }
    )
    foreach ($entry in $secretKeys) {
        $current = Get-DotEnvValue -Key $entry.Key
        if (Test-PlaceholderValue -Value $current) {
            Set-DotEnvValue -Key $entry.Key -Value (New-SafeSecret -Length $entry.Length)
            Write-Warn "Generated production value for $($entry.Key) (not shown)"
        }
    }

    if ($AllowedOrigin -and $AllowedOrigin.Count -gt 0) {
        Set-DotEnvValue -Key "ALLOWED_ORIGINS" -Value (ConvertTo-OriginJson -Origins $AllowedOrigin)
    }

    Set-DotEnvValue -Key "API_PORT" -Value ([string]$ApiPort)
    Set-DotEnvValue -Key "API_HOST_BIND" -Value "0.0.0.0"
    Set-DotEnvValue -Key "POSTGRES_PORT" -Value ([string]$PostgresPort)
    Set-DotEnvValue -Key "REQUIRE_DATABASE" -Value "true"
    Set-DotEnvValue -Key "SEED_DEFAULT_USERS" -Value "true"
    Set-DotEnvValue -Key "PORTAL_MOUNT_ENABLED" -Value "false"
    Set-DotEnvValue -Key "SEED_REFERENCE_DATA_ON_STARTUP" -Value "true"
    Set-DotEnvValue -Key "USE_POSTGRES_UNITS" -Value "true"
    Set-DotEnvValue -Key "CORS_ALLOW_CREDENTIALS" -Value "false"

    $origins = Get-DotEnvValue -Key "ALLOWED_ORIGINS"
    if (-not $origins) {
        Fail-Setup "ALLOWED_ORIGINS is missing in .env.production." "Rerun with -AllowedOrigin https://your-domain.example."
    }
    if ($origins -match '\*') {
        Fail-Setup "ALLOWED_ORIGINS cannot contain '*' for production." "Set explicit HTTPS origins."
    }
    if ((Test-OriginIsLocalOrWildcard -OriginsText $origins) -and -not $AllowLocalhostOrigin) {
        Fail-Setup "ALLOWED_ORIGINS is localhost/default, not production-safe." "Rerun with -AllowedOrigin https://your-domain.example, or add -AllowLocalhostOrigin only for local verification."
    }

    Write-Ok ".env.production is ready"
}

function Wait-ForPostgres {
    Write-Step "Waiting for PostgreSQL readiness ..."
    $deadline = (Get-Date).AddSeconds(120)
    while ((Get-Date) -lt $deadline) {
        try {
            $null = Invoke-Compose -Arguments @("exec", "-T", "postgres", "pg_isready", "-U", "sds", "-d", "sds") -Capture
            Write-Ok "PostgreSQL is ready"
            return
        }
        catch { Start-Sleep -Seconds 2 }
    }
    Fail-Setup "PostgreSQL did not become ready in time." "Run .\scripts\dev.ps1 ProductionLogs and inspect postgres logs."
}

function Wait-ForUrl {
    param([Parameter(Mandatory = $true)][string]$Url, [Parameter(Mandatory = $true)][string]$Name, [int]$TimeoutSeconds = 120)
    Write-Step "Waiting for $Name ($Url) ..."
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        try {
            $null = Invoke-WebRequest -Uri $Url -Method Get -TimeoutSec 5 -UseBasicParsing
            Write-Ok "$Name is available"
            return
        }
        catch { Start-Sleep -Seconds 2 }
    }
    Fail-Setup "Timeout waiting for $Name." "Run .\scripts\dev.ps1 ProductionLogs and inspect API logs."
}

function Invoke-Json {
    param([Parameter(Mandatory = $true)][ValidateSet("GET", "POST")][string]$Method, [Parameter(Mandatory = $true)][string]$Url, [hashtable]$Headers, $Body)
    $args = @{
        Uri = $Url
        Method = $Method
        TimeoutSec = 30
        ContentType = "application/json"
        UseBasicParsing = $true
    }
    if ($Headers) { $args["Headers"] = $Headers }
    if ($null -ne $Body) { $args["Body"] = ($Body | ConvertTo-Json -Depth 8 -Compress) }
    return Invoke-RestMethod @args
}

function Get-StatusCode {
    param([Parameter(Mandatory = $true)][string]$Url)
    try {
        $response = Invoke-WebRequest -Uri $Url -Method Get -TimeoutSec 20 -UseBasicParsing
        return [int]$response.StatusCode
    }
    catch {
        if ($_.Exception.Response) {
            return [int]$_.Exception.Response.StatusCode
        }
        throw
    }
}

function Test-ProductionApi {
    Write-Step "Verifying production API ..."
    $null = Invoke-WebRequest -Uri "$ApiBaseUrl/docs" -TimeoutSec 30 -UseBasicParsing
    Write-Ok "/docs is available"

    $adminPassword = Get-DotEnvValue -Key "BOOTSTRAP_ADMIN_PASSWORD"
    try {
        $login = Invoke-Json -Method POST -Url "$ApiBaseUrl/auth/login" -Body @{
            username = "admin"
            password = $adminPassword
        }
    }
    catch {
        Fail-Setup "Admin login failed." "If this DB was initialized with an older password, rerun with -ResetDatabase or restore the original .env.production."
    }
    if (-not $login.access_token) {
        Fail-Setup "Admin login did not return an access token." "Check BOOTSTRAP_ADMIN_PASSWORD in .env.production."
    }
    Write-Ok "Admin login works"

    $concepts = Invoke-Json -Method GET -Url "$ApiBaseUrl/api/v1/concepts?limit=1" -Headers @{ Authorization = "Bearer $($login.access_token)" }
    if ($null -eq $concepts.total) {
        Fail-Setup "Concept list did not return a paginated response." "Check semantic/reference bootstrap logs."
    }
    $indicators = Invoke-Json -Method GET -Url "$ApiBaseUrl/api/v1/indicators?limit=1" -Headers @{ Authorization = "Bearer $($login.access_token)" }
    if ($null -ne $indicators.total -and [int]$concepts.total -lt [int]$indicators.total) {
        Fail-Setup "Semantic concept projection is incomplete." "Run project-semantic-catalog and gate-semantic-catalog-projection before accepting production."
    }
    Write-Ok "Concept list works and covers the active indicator catalogue"
}

try {
    Write-Step "SDS optional self-host reference setup"
    Write-Warn "Docker Compose is an optional quickstart/self-host helper, not the required SDS API install model."

    Ensure-Command -Command "docker" -Name "Docker CLI"
    Ensure-DockerCompose
    Ensure-DockerEngine
    Ensure-PathExists -Path $ComposeFile -Name "compose.production.yml"
    Ensure-PathExists -Path (Join-Path $ApiRoot "Dockerfile") -Name "Dockerfile"

    Ensure-ProductionEnv
    Ensure-Port -Port $ApiPort -Name "API"
    Ensure-Port -Port $PostgresPort -Name "PostgreSQL"

    Write-Step "Validating production compose configuration ..."
    $null = Invoke-Compose -Arguments @("config") -Capture
    Write-Ok "Production compose configuration is valid"

    if ($ResetDatabase) {
        Write-Warn "-ResetDatabase requested: production database volume will be removed."
        Invoke-Compose -Arguments @("down", "--remove-orphans")
        Remove-ComposeDatabaseVolume
    }

    Write-Step "Starting PostgreSQL ..."
    Invoke-Compose -Arguments @("up", "-d", "postgres")
    Wait-ForPostgres

    if ($Rebuild) {
        Write-Step "Rebuilding production API image without cache ..."
        Invoke-Compose -Arguments @("build", "--no-cache", "api")
    }

    Write-Step "Starting production API ..."
    $apiUpArgs = @("up", "-d")
    if (-not $NoBuild) { $apiUpArgs += "--build" }
    $apiUpArgs += "api"
    Invoke-Compose -Arguments $apiUpArgs

    Wait-ForUrl -Url "$ApiBaseUrl/healthz" -Name "API health"
    Wait-ForUrl -Url "$ApiBaseUrl/ready" -Name "API readiness"

    if (-not $SkipVerify) {
        Test-ProductionApi
    }
    else {
        Write-Warn "Verification skipped by -SkipVerify"
    }

    Write-Step "SDS optional self-host reference is ready"
    Write-Host ""
    Write-Host "  API docs:    http://127.0.0.1:$ApiPort/docs"
    Write-Host "  Health:      http://127.0.0.1:$ApiPort/healthz"
    Write-Host "  Ready:       http://127.0.0.1:$ApiPort/ready"
    Write-Host ""
    Write-Host "  Admin user:  admin"
    Write-Host "  Admin pass:  stored in api/.env.production as BOOTSTRAP_ADMIN_PASSWORD"
    Write-Host ""
    Write-Host "  Logs:        .\scripts\dev.ps1 ProductionLogs"
    Write-Host "  Stop:        .\scripts\dev.ps1 ProductionDown"
    if ($ProjectName -or $EnvFile -ne $DefaultEnvFile) {
        Write-Host "  Project:     $(Get-ComposeProjectName)"
        Write-Host "  Env file:    $EnvFile"
        Write-Host "  Logs drill:  .\scripts\dev.ps1 ProductionLogs -ProjectName $(Get-ComposeProjectName) -EnvFile `"$EnvFile`""
        Write-Host "  Stop drill:  .\scripts\dev.ps1 ProductionDown -ProjectName $(Get-ComposeProjectName) -EnvFile `"$EnvFile`" -RemoveVolumes"
    }
    Write-Host "  Setup log:   $LogFile"
    Write-Host ""
    Stop-SetupTranscript
}
catch {
    Fail-Setup $_.Exception.Message
}
