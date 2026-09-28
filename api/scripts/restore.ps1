param(
    [Parameter(Mandatory = $true, Position = 0)]
    [string]$BackupFile,
    [switch]$Force,
    [string]$ProjectName,
    [string]$EnvFile,
    [string]$ComposeFile,
    [string]$ManifestPath,
    [switch]$RequireManifest,
    [switch]$AllowUnverifiedRestore,
    [switch]$AllowTargetMismatch,
    [switch]$SkipVerify,
    [ValidateRange(0, [int]::MaxValue)]
    [int]$TimeoutSeconds = 0
)

$ErrorActionPreference = "Stop"

# CLI switches include -RequireManifest and -SkipVerify for non-interactive drills.
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ApiRoot = (Resolve-Path (Join-Path $ScriptDir "..")).Path

if (-not (Test-Path -LiteralPath $BackupFile)) {
    throw "Backup file not found: $BackupFile"
}

$resolvedBackup = (Resolve-Path -LiteralPath $BackupFile).Path
if (-not $EnvFile) {
    $EnvFile = ".env"
}
if (-not $ComposeFile) {
    $ComposeFile = "compose.yml"
}

function Build-ComposeArgs {
    param(
        [string]$ProjectName,
        [string]$EnvFile,
        [string]$ComposeFile
    )

    $args = @("compose", "--env-file", $EnvFile, "-f", $ComposeFile)
    if ($ProjectName) {
        $args += @("-p", $ProjectName)
    }
    return $args
}

function Test-ComposeApiRunning {
    param([Parameter(Mandatory = $true)][string[]]$ComposeArgs)

    $output = & docker @($ComposeArgs + @("ps", "api", "--format", "json")) 2>$null
    if ($LASTEXITCODE -ne 0) {
        return $false
    }
    $text = ($output | Out-String).Trim()
    if (-not $text) {
        return $false
    }

    try {
        foreach ($service in @($text | ConvertFrom-Json)) {
            if ($service.State -eq "running" -or "$($service.Status)" -match "^Up\b") {
                return $true
            }
        }
    }
    catch {
        return ($text -match '"State"\s*:\s*"running"' -or $text -match '\bUp\b')
    }
    return $false
}

function Start-DockerProcess {
    param(
        [string[]]$Arguments,
        [string]$RedirectStandardInput,
        [string]$RedirectStandardOutput,
        [string]$RedirectStandardError,
        [int]$TimeoutSeconds = 0
    )

    $processInfo = [System.Diagnostics.ProcessStartInfo]::new()
    $processInfo.FileName = "docker"
    $processInfo.UseShellExecute = $false
    $processInfo.WorkingDirectory = (Get-Location).Path
    if ($RedirectStandardInput) {
        $processInfo.RedirectStandardInput = $true
    }
    if ($RedirectStandardOutput) {
        $processInfo.RedirectStandardOutput = $true
    }
    if ($RedirectStandardError) {
        $processInfo.RedirectStandardError = $true
    }
    foreach ($argument in $Arguments) {
        [void]$processInfo.ArgumentList.Add($argument)
    }

    $process = [System.Diagnostics.Process]::Start($processInfo)
    $inputStream = $null
    $stdinTask = $null
    $stdinClosed = $false
    $stdoutStream = $null
    $stderrStream = $null
    $stdoutTask = $null
    $stderrTask = $null
    try {
        if ($RedirectStandardInput) {
            $inputStream = [System.IO.File]::OpenRead($RedirectStandardInput)
            if ($TimeoutSeconds -gt 0) {
                $stdinTask = $inputStream.CopyToAsync($process.StandardInput.BaseStream)
            }
            else {
                $inputStream.CopyTo($process.StandardInput.BaseStream)
                $process.StandardInput.Close()
                $stdinClosed = $true
            }
        }
        $stdoutStream = if ($RedirectStandardOutput) { [System.IO.File]::Create($RedirectStandardOutput) } else { $null }
        $stderrStream = if ($RedirectStandardError) { [System.IO.File]::Create($RedirectStandardError) } else { $null }
        $stdoutTask = if ($stdoutStream) { $process.StandardOutput.BaseStream.CopyToAsync($stdoutStream) } else { $null }
        $stderrTask = if ($stderrStream) { $process.StandardError.BaseStream.CopyToAsync($stderrStream) } else { $null }

        if ($TimeoutSeconds -gt 0) {
            $deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
            while (-not $process.HasExited) {
                if ($stdinTask -and $stdinTask.IsCompleted -and -not $stdinClosed) {
                    try { $stdinTask.Wait() } catch {}
                    try { $process.StandardInput.Close() } catch {}
                    $stdinClosed = $true
                    if ($inputStream) {
                        $inputStream.Dispose()
                        $inputStream = $null
                    }
                }
                if ([DateTime]::UtcNow -ge $deadline) {
                    try {
                        if (-not $process.HasExited) {
                            $process.Kill()
                        }
                    }
                    catch {}
                    [void]$process.WaitForExit(5000)
                    throw "Docker process timed out after $TimeoutSeconds seconds."
                }
                Start-Sleep -Milliseconds 100
            }
        }
        else {
            $process.WaitForExit()
        }

        if ($stdinTask) {
            try { [void]$stdinTask.Wait(5000) } catch {}
        }
        return $process.ExitCode
    }
    finally {
        if (-not $stdinClosed) {
            try { $process.StandardInput.Close() } catch {}
        }
        if ($stdoutTask) {
            try { [void]$stdoutTask.Wait(5000) } catch {}
        }
        if ($stderrTask) {
            try { [void]$stderrTask.Wait(5000) } catch {}
        }
        if ($inputStream) { $inputStream.Dispose() }
        if ($stdoutStream) { $stdoutStream.Dispose() }
        if ($stderrStream) { $stderrStream.Dispose() }
    }
}

if (-not $ManifestPath) {
    $sidecarManifest = "$resolvedBackup.manifest.json"
    if (Test-Path -LiteralPath $sidecarManifest) {
        $ManifestPath = $sidecarManifest
    }
}

if ($ManifestPath) {
    if (-not (Test-Path -LiteralPath $ManifestPath)) {
        throw "Manifest file not found: $ManifestPath"
    }
    $manifest = Get-Content -LiteralPath $ManifestPath -Raw | ConvertFrom-Json
    if (-not $manifest.sha256) {
        throw "Manifest does not contain sha256: $ManifestPath"
    }
    $actualHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $resolvedBackup).Hash.ToUpperInvariant()
    $expectedHash = "$($manifest.sha256)".ToUpperInvariant()
    if ($actualHash -ne $expectedHash) {
        throw "Backup SHA-256 mismatch for $resolvedBackup. Expected $expectedHash, actual $actualHash."
    }
    Write-Host "Manifest checksum OK: $ManifestPath"
    # F13 M1: remember the manifest's recorded target for validation before restore.
    $manifestDb = "$($manifest.postgres_db)"
    $manifestUser = "$($manifest.postgres_user)"
}
elseif ($RequireManifest) {
    throw "Manifest required but not found. Expected sidecar: $resolvedBackup.manifest.json"
}
else {
    # F13 M2: never perform an unverified DESTRUCTIVE restore in automation. A
    # non-interactive (-Force) run without a manifest checksum must opt into the risk
    # explicitly via -AllowUnverifiedRestore (break-glass).
    if ($Force -and -not $AllowUnverifiedRestore) {
        throw "Refusing unverified non-interactive restore: no manifest checksum for $resolvedBackup. Provide a sidecar manifest, or pass -AllowUnverifiedRestore for break-glass use."
    }
    Write-Warning "Restoring without manifest checksum validation."
}

if (-not $Force) {
    if ([Console]::IsInputRedirected) {
        throw "Refusing non-interactive restore without -Force."
    }
    Write-Warning "This will restore into the active compose PostgreSQL database and may drop existing objects."
    $confirmation = Read-Host "Type RESTORE to continue"
    if ($confirmation -ne "RESTORE") {
        throw "Restore cancelled."
    }
}

$stderrPath = [System.IO.Path]::GetTempFileName()
$restoreOutPath = [System.IO.Path]::GetTempFileName()
$verifySqlPath = [System.IO.Path]::GetTempFileName()
$verifyOutPath = [System.IO.Path]::GetTempFileName()
$composeArgs = Build-ComposeArgs -ProjectName $ProjectName -EnvFile $EnvFile -ComposeFile $ComposeFile

Push-Location $ApiRoot
try {
    if (Test-ComposeApiRunning -ComposeArgs $composeArgs) {
        throw "Refusing restore while the compose api service is running. Stop it first with docker compose ... stop api, then restart it after a verified restore."
    }

    # F13 M1: validate the manifest's recorded target against the live target container so
    # a valid backup is never silently restored into the wrong database/user.
    if ($manifestDb) {
        $targetInfoPath = [System.IO.Path]::GetTempFileName()
        try {
            $targetArgs = $composeArgs + @("exec", "-T", "postgres", "sh", "-lc", 'printf "%s\t%s" "$POSTGRES_DB" "$POSTGRES_USER"')
            [void](Start-DockerProcess -Arguments $targetArgs -RedirectStandardOutput $targetInfoPath -RedirectStandardError $stderrPath -TimeoutSeconds $TimeoutSeconds)
            $targetInfo = (Get-Content -LiteralPath $targetInfoPath -Raw -ErrorAction SilentlyContinue)
        }
        finally {
            Remove-Item -LiteralPath $targetInfoPath -ErrorAction SilentlyContinue
        }
        $targetParts = ("$targetInfo").Split("`t")
        $targetDb = $targetParts[0]
        $targetUser = if ($targetParts.Length -gt 1) { $targetParts[1] } else { "" }
        if ($targetDb -and (($manifestDb -ne $targetDb) -or ($manifestUser -ne $targetUser))) {
            $msg = "Restore target mismatch: manifest target db=$manifestDb user=$manifestUser ; live target db=$targetDb user=$targetUser."
            if (-not $AllowTargetMismatch) {
                throw "$msg Refusing restore into a different database/user. Pass -AllowTargetMismatch to override."
            }
            Write-Warning "$msg Proceeding (-AllowTargetMismatch)."
        }
    }

    $arguments = $composeArgs + @("exec", "-T", "postgres", "sh", "-lc", 'psql -q -U $POSTGRES_USER -d $POSTGRES_DB --set ON_ERROR_STOP=on --single-transaction')
    $exitCode = Start-DockerProcess -Arguments $arguments -RedirectStandardInput $resolvedBackup -RedirectStandardOutput $restoreOutPath -RedirectStandardError $stderrPath -TimeoutSeconds $TimeoutSeconds
    $stderr = if (Test-Path $stderrPath) { Get-Content -LiteralPath $stderrPath -Raw -ErrorAction SilentlyContinue } else { "" }
    if ($exitCode -ne 0) {
        throw "Restore failed. $stderr"
    }
    if ($stderr) {
        Write-Warning $stderr.Trim()
    }

    if (-not $SkipVerify) {
        @"
DO `$`$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM alembic_version) THEN
    RAISE EXCEPTION 'missing alembic_version row';
  END IF;
END `$`$;
SELECT 'alembic_version=' || version_num FROM alembic_version LIMIT 1;
SELECT 'indicators=' || COUNT(*) FROM indicators;
SELECT 'units=' || COUNT(*) FROM units;
SELECT 'concepts=' || COUNT(*) FROM concepts;
SELECT 'user_accounts=' || COUNT(*) FROM user_accounts;
"@ | Set-Content -LiteralPath $verifySqlPath -Encoding UTF8
        $verifyArgs = $composeArgs + @("exec", "-T", "postgres", "sh", "-lc", 'psql -q -U $POSTGRES_USER -d $POSTGRES_DB --set ON_ERROR_STOP=on -At')
        $verifyExitCode = Start-DockerProcess -Arguments $verifyArgs -RedirectStandardInput $verifySqlPath -RedirectStandardOutput $verifyOutPath -RedirectStandardError $stderrPath -TimeoutSeconds $TimeoutSeconds
        $verifyStderr = if (Test-Path $stderrPath) { Get-Content -LiteralPath $stderrPath -Raw -ErrorAction SilentlyContinue } else { "" }
        if ($verifyExitCode -ne 0) {
            throw "Post-restore verification failed. $verifyStderr"
        }
        $verifyOut = Get-Content -LiteralPath $verifyOutPath -Raw -ErrorAction SilentlyContinue
        if ($verifyOut) {
            Write-Host $verifyOut.Trim()
        }
        Write-Host "Post-restore verification OK"
    }
    else {
        Write-Host "Post-restore verification skipped by -SkipVerify"
    }
}
finally {
    Pop-Location
    Remove-Item -LiteralPath $stderrPath -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $restoreOutPath -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $verifySqlPath -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $verifyOutPath -Force -ErrorAction SilentlyContinue
}

Write-Host "Restore OK: $resolvedBackup"
