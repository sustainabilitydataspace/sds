param(
    [Parameter(Position = 0)]
    [ValidateSet(
        "Setup",
        "Install",
        "InstallDev",
        "PrepareData",
        "ImportIndicators",
        "ImportMappings",
        "Test",
        "QuickTest",
        "TestCoverage",
        "Lint",
        "Format",
        "Start",
        "StartProd",
        "Stop",
        "Status",
        "Up",
        "Down",
        "Logs",
        "ProductionInstall",
        "ProductionLogs",
        "ProductionDown",
        "ProductionPs",
        "Ps",
        "Smoke",
        "Backup",
        "Restore",
        "Info"
    )]
    [string]$Action = "Info",
    [Parameter(Position = 1)]
    [ValidateSet("minimal")]
    [string]$Profile = "minimal",
    [Parameter(Position = 2)]
    [string]$BackupFile,
    [Parameter()]
    [string]$OutDir,
    [Parameter()]
    [string]$ManifestPath,
    [Parameter()]
    [switch]$RequireManifest,
    [Parameter()]
    [ValidateRange(0, [int]::MaxValue)]
    [int]$TimeoutSeconds = 0,
    [Parameter()]
    [switch]$Resume,
    [Parameter()]
    [switch]$Clean,
    [Parameter()]
    [switch]$ResetDatabase,
    [Parameter()]
    [switch]$Force,
    [Parameter()]
    [switch]$NoBuild,
    [Parameter()]
    [switch]$Rebuild,
    [Parameter()]
    [switch]$SkipSmoke,
    [Parameter()]
    [switch]$SkipVerify,
    [Parameter()]
    [switch]$AllowLocalhostOrigin,
    [Parameter()]
    [string[]]$AllowedOrigin,
    [Parameter()]
    [int]$ApiPort,
    [Parameter()]
    [int]$PostgresPort,
    [Parameter()]
    [string]$ProjectName,
    [Parameter()]
    [Alias("EnvFile")]
    [string]$ProductionEnvFile,
    [Parameter()]
    [switch]$RemoveVolumes
)

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ApiRoot = (Resolve-Path (Join-Path $ScriptDir "..")).Path
$RepoRoot = (Resolve-Path (Join-Path $ApiRoot "..\..")).Path
$EnvFile = Join-Path $ApiRoot ".env"
$VenvDir = Join-Path $ApiRoot ".venv"
$VenvPython = Join-Path $VenvDir "Scripts\python.exe"
$CoverageFailUnder = "97.01"
$CoverageJson = "artifacts/coverage/coverage.json"

function Assert-SupportedPython {
    param([Parameter(Mandatory = $true)][string]$PythonPath)

    $version = & $PythonPath -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}')"
    if ($LASTEXITCODE -ne 0) {
        throw "Unable to inspect Python version at $PythonPath."
    }

    $parts = $version.Trim().Split(".")
    $major = [int]$parts[0]
    $minor = [int]$parts[1]
    if ($major -ne 3 -or $minor -lt 10 -or $minor -ge 14) {
        throw "Unsupported Python $version. SDS API local Python supports 3.10-3.13. Install Python 3.13 or 3.12 and recreate .venv."
    }
}

function Invoke-InDirectory {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][scriptblock]$Script
    )
    Push-Location $Path
    try {
        & $Script
    }
    finally {
        Pop-Location
    }
}

function Ensure-Venv {
    if (-not (Test-Path $VenvPython)) {
        Invoke-InDirectory -Path $ApiRoot -Script {
            Assert-SupportedPython -PythonPath "python"
            & python -m venv .venv
            if ($LASTEXITCODE -ne 0) {
                throw "Failed to create .venv with python."
            }
        }
    }
    Assert-SupportedPython -PythonPath $VenvPython
}

function Invoke-VenvPython {
    param([Parameter(ValueFromRemainingArguments = $true)][string[]]$Arguments)
    Ensure-Venv
    Invoke-InDirectory -Path $ApiRoot -Script {
        & $VenvPython @Arguments
        if ($LASTEXITCODE -ne 0) {
            throw "Python command failed: $($Arguments -join ' ')"
        }
    }
}

function Ensure-EnvFile {
    $example = Join-Path $ApiRoot ".env.example"
    if (-not (Test-Path $EnvFile) -and (Test-Path $example)) {
        Copy-Item -LiteralPath $example -Destination $EnvFile
        Write-Host "Created $EnvFile from .env.example"
    }
}

function Get-DotEnvValue {
    param([Parameter(Mandatory = $true)][string]$Key)
    if (-not (Test-Path $EnvFile)) {
        return $null
    }

    foreach ($line in Get-Content -LiteralPath $EnvFile) {
        if ($line -match "^\s*#") {
            continue
        }
        if ($line -match "^\s*$([regex]::Escape($Key))=(.*)$") {
            $value = $matches[1].Trim()
            if ($value.StartsWith('"') -and $value.EndsWith('"')) {
                return $value.Substring(1, $value.Length - 2)
            }
            return $value
        }
    }

    return $null
}

function Get-PostgresPort {
    $postgresPort = [Environment]::GetEnvironmentVariable("POSTGRES_PORT", "Process")
    if (-not $postgresPort) {
        $postgresPort = Get-DotEnvValue -Key "POSTGRES_PORT"
    }
    if (-not $postgresPort) {
        return "5432"
    }

    return $postgresPort
}

function Get-ComposeDatabaseUrl {
    $postgresPassword = Get-DotEnvValue -Key "POSTGRES_PASSWORD"
    if (-not $postgresPassword) {
        throw "POSTGRES_PASSWORD is missing in api/.env"
    }

    $postgresPort = Get-PostgresPort
    return "postgresql://sds:$postgresPassword@127.0.0.1:$postgresPort/sds"
}

function Invoke-IndicatorImport {
    param([switch]$Resume)

    $csvPath = Join-Path $RepoRoot "data\processed\e1_dataset_register.csv"
    if (-not (Test-Path $csvPath)) {
        Write-Host "Processed dataset not found. Preparing Atomizer data first..."
        Invoke-InDirectory -Path $RepoRoot -Script {
            & python scripts\prepare_atomizer_for_import.py
            if ($LASTEXITCODE -ne 0) {
                throw "prepare_atomizer_for_import.py failed."
            }
        }
    }

    Ensure-EnvFile

    $args = @(
        "scripts\import_indicators.py",
        "--csv",
        $csvPath,
        "--db-url",
        (Get-ComposeDatabaseUrl)
    )
    if ($Resume) {
        $args += "--resume"
    }

    Invoke-VenvPython @args
}

function Invoke-MappingImport {
    Ensure-EnvFile
    $args = @(
        "scripts\import_standard_mappings.py",
        "--db-url",
        (Get-ComposeDatabaseUrl)
    )
    Invoke-VenvPython @args
}

function Invoke-Compose {
    param(
        [Parameter(Mandatory = $true)][string[]]$Arguments,
        [hashtable]$TemporaryEnvironment
    )

    $saved = @{}
    if ($TemporaryEnvironment) {
        foreach ($entry in $TemporaryEnvironment.GetEnumerator()) {
            $saved[$entry.Key] = [Environment]::GetEnvironmentVariable($entry.Key, "Process")
            [Environment]::SetEnvironmentVariable($entry.Key, $entry.Value, "Process")
        }
    }

    try {
        Invoke-InDirectory -Path $ApiRoot -Script {
            & docker compose --env-file .env -f compose.yml @Arguments
            if ($LASTEXITCODE -ne 0) {
                throw "docker compose failed: $($Arguments -join ' ')"
            }
        }
    }
    finally {
        if ($TemporaryEnvironment) {
            foreach ($entry in $TemporaryEnvironment.GetEnumerator()) {
                [Environment]::SetEnvironmentVariable($entry.Key, $saved[$entry.Key], "Process")
            }
        }
    }
}

function Invoke-ProductionCompose {
    param(
        [Parameter(Mandatory = $true)][string[]]$Arguments
    )

    # Historical action name: this is an optional self-host reference profile,
    # not the required SDS API dependency-install or deployment model.
    $envFilePath = ".env.production"
    if ($ProductionEnvFile) {
        if ([System.IO.Path]::IsPathRooted($ProductionEnvFile)) {
            $envFilePath = $ProductionEnvFile
        }
        else {
            $envFilePath = Join-Path $ApiRoot $ProductionEnvFile
        }
    }
    Invoke-InDirectory -Path $ApiRoot -Script {
        $composeArgs = @("compose")
        if ($ProjectName) {
            $composeArgs += @("-p", $ProjectName)
        }
        $composeArgs += @("--env-file", $envFilePath, "-f", "compose.production.yml")
        $composeArgs += $Arguments
        & docker @composeArgs
        if ($LASTEXITCODE -ne 0) {
            throw "production docker compose failed: $($Arguments -join ' ')"
        }
    }
}

switch ($Action) {
    "Setup" {
        Ensure-Venv
        Invoke-VenvPython -m pip install --upgrade pip
        Invoke-VenvPython -m pip install -r requirements.txt
        Ensure-EnvFile
        Write-Host "Setup complete. Edit api/.env before first start if needed."
    }
    "Install" {
        Invoke-VenvPython -m pip install -r requirements.txt
    }
    "InstallDev" {
        Invoke-VenvPython -m pip install -r requirements-dev.txt
    }
    "PrepareData" {
        Invoke-InDirectory -Path $RepoRoot -Script {
            & python scripts\prepare_atomizer_for_import.py
            if ($LASTEXITCODE -ne 0) {
                throw "prepare_atomizer_for_import.py failed."
            }
        }
    }
    "ImportIndicators" {
        Invoke-IndicatorImport -Resume:$Resume
    }
    "ImportMappings" {
        Invoke-MappingImport
    }
    "Test" {
        Invoke-VenvPython -m pytest tests/ -v --tb=short
    }
    "QuickTest" {
        Invoke-VenvPython -m pytest tests/ -x --tb=line
    }
    "TestCoverage" {
        Invoke-VenvPython -c "from pathlib import Path; Path('artifacts/coverage').mkdir(parents=True, exist_ok=True)"
        Invoke-VenvPython -m pytest tests/ --cov=src --cov-report=term --cov-report=html --cov-report=json:$CoverageJson --cov-fail-under=$CoverageFailUnder
    }
    "Lint" {
        Invoke-VenvPython -m flake8 src/ tests/
        Invoke-VenvPython -m black --check src/ tests/
        Invoke-VenvPython -m isort --check-only src/ tests/
    }
    "Format" {
        Invoke-VenvPython -m black src/ tests/
        Invoke-VenvPython -m isort src/ tests/
    }
    "Start" {
        Ensure-EnvFile
        Invoke-VenvPython -m uvicorn src.api.main:app --reload --host 127.0.0.1 --port 8090 --no-proxy-headers
    }
    "StartProd" {
        Ensure-EnvFile
        Invoke-VenvPython -m uvicorn src.api.main:app --host 0.0.0.0 --port 8090 --no-proxy-headers
    }
    "Stop" {
        $processes = Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" |
            Where-Object { $_.CommandLine -like "*uvicorn src.api.main:app*" }
        if (-not $processes) {
            Write-Host "No local uvicorn process found."
            break
        }
        $processes | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
        Write-Host "Stopped local uvicorn process(es)."
    }
    "Status" {
        $postgresPort = Get-PostgresPort
        $processes = Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" |
            Where-Object { $_.CommandLine -like "*uvicorn src.api.main:app*" }
        if ($processes) {
            Write-Host "API process: running"
            $processes | Select-Object ProcessId, CommandLine | Format-Table -AutoSize
        }
        else {
            Write-Host "API process: stopped"
        }
        Write-Host "URLs:"
        Write-Host "  API:    http://127.0.0.1:8090"
        Write-Host "  Docs:   http://127.0.0.1:8090/docs"
        Write-Host "  Health: http://127.0.0.1:8090/healthz"
        Write-Host "  PostgreSQL (compose host): 127.0.0.1:$postgresPort"
        Invoke-Compose -Arguments @("ps")
    }
    "Up" {
        Ensure-EnvFile
        Invoke-Compose -Arguments @("up", "-d", "--build")
    }
    "Down" {
        Ensure-EnvFile
        Invoke-Compose -Arguments @("down", "--remove-orphans")
    }
    "Logs" {
        Ensure-EnvFile
        Invoke-Compose -Arguments @("logs", "-f", "--tail=200")
    }
    "ProductionInstall" {
        $prodParams = @{}
        if ($AllowedOrigin) { $prodParams["AllowedOrigin"] = $AllowedOrigin }
        if ($AllowLocalhostOrigin) { $prodParams["AllowLocalhostOrigin"] = $true }
        if ($ApiPort) { $prodParams["ApiPort"] = $ApiPort }
        if ($PostgresPort) { $prodParams["PostgresPort"] = $PostgresPort }
        if ($ProjectName) { $prodParams["ProjectName"] = $ProjectName }
        if ($ProductionEnvFile) { $prodParams["EnvFile"] = $ProductionEnvFile }
        if ($ResetDatabase) { $prodParams["ResetDatabase"] = $true }
        if ($NoBuild) { $prodParams["NoBuild"] = $true }
        if ($Rebuild) { $prodParams["Rebuild"] = $true }
        if ($SkipVerify) { $prodParams["SkipVerify"] = $true }
        & (Join-Path $ScriptDir "setup_production_server.ps1") @prodParams
        if ($LASTEXITCODE -ne 0) {
            throw "Production install failed."
        }
    }
    "ProductionLogs" {
        Invoke-ProductionCompose -Arguments @("logs", "-f", "--tail=200")
    }
    "ProductionDown" {
        $downArgs = @("down", "--remove-orphans")
        if ($RemoveVolumes) { $downArgs += "-v" }
        Invoke-ProductionCompose -Arguments $downArgs
    }
    "ProductionPs" {
        Invoke-ProductionCompose -Arguments @("ps")
    }
    "Ps" {
        Ensure-EnvFile
        Invoke-Compose -Arguments @("ps")
    }
    "Smoke" {
        & (Join-Path $ScriptDir "smoke_stack.ps1") -Profile $Profile
        if ($LASTEXITCODE -ne 0) {
            throw "Smoke test failed."
        }
    }
    "Backup" {
        $backupParams = @{}
        if ($OutDir) { $backupParams["OutDir"] = $OutDir }
        if ($ProjectName) { $backupParams["ProjectName"] = $ProjectName }
        if ($TimeoutSeconds -gt 0) { $backupParams["TimeoutSeconds"] = $TimeoutSeconds }
        if ($ProductionEnvFile) {
            $backupParams["EnvFile"] = $ProductionEnvFile
            $backupParams["ComposeFile"] = "compose.production.yml"
        }
        & (Join-Path $ScriptDir "backup.ps1") @backupParams
        if ($LASTEXITCODE -ne 0) {
            throw "Backup failed."
        }
    }
    "Restore" {
        if (-not $BackupFile) {
            throw "BackupFile is required for Restore."
        }
        $restoreParams = @{ BackupFile = $BackupFile }
        if ($Force) { $restoreParams["Force"] = $true }
        if ($ProjectName) { $restoreParams["ProjectName"] = $ProjectName }
        if ($ManifestPath) { $restoreParams["ManifestPath"] = $ManifestPath }
        if ($RequireManifest) { $restoreParams["RequireManifest"] = $true }
        if ($SkipVerify) { $restoreParams["SkipVerify"] = $true }
        if ($TimeoutSeconds -gt 0) { $restoreParams["TimeoutSeconds"] = $TimeoutSeconds }
        if ($ProductionEnvFile) {
            $restoreParams["EnvFile"] = $ProductionEnvFile
            $restoreParams["ComposeFile"] = "compose.production.yml"
        }
        & (Join-Path $ScriptDir "restore.ps1") @restoreParams
        if ($LASTEXITCODE -ne 0) {
            throw "Restore failed."
        }
    }
    "Info" {
        Write-Host "SustainabilityDataSpace API Windows helper"
        Write-Host ""
        Write-Host "Common actions:"
        Write-Host "  .\scripts\dev.ps1 Setup"
        Write-Host "  .\scripts\dev.ps1 Start"
        Write-Host "  .\scripts\dev.ps1 ImportIndicators"
        Write-Host "  .\scripts\dev.ps1 ImportMappings"
        Write-Host "  .\scripts\dev.ps1 Test"
        Write-Host "  .\scripts\dev.ps1 Up"
        Write-Host "  .\scripts\dev.ps1 ProductionInstall -AllowedOrigin https://your-domain.example  # optional self-host reference"
        Write-Host "  .\scripts\dev.ps1 ProductionInstall -AllowedOrigin http://localhost:8093 -AllowLocalhostOrigin -ApiPort 8093 -PostgresPort 55434 -ProjectName sds-a02-prod -EnvFile ..\.local_artifacts\a02-prod\.env.production  # disposable drill"
        Write-Host "  .\scripts\dev.ps1 ProductionDown -ProjectName sds-a02-prod -EnvFile ..\.local_artifacts\a02-prod\.env.production -RemoveVolumes  # disposable teardown"
        Write-Host "  .\scripts\dev.ps1 Smoke"
        Write-Host "  .\scripts\dev.ps1 Backup"
        Write-Host ""
        Write-Host "Repo root: $RepoRoot"
        Write-Host "API root:  $ApiRoot"
    }
}
