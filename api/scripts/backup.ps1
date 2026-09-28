param(
    [string]$OutDir,
    [string]$ProjectName,
    [string]$EnvFile,
    [string]$ComposeFile,
    [ValidateRange(0, [int]::MaxValue)]
    [int]$TimeoutSeconds = 0
)

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ApiRoot = (Resolve-Path (Join-Path $ScriptDir "..")).Path

if (-not $OutDir) {
    $OutDir = Join-Path $ApiRoot "backups"
}
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

function Start-DockerProcess {
    param(
        [string[]]$Arguments,
        [string]$RedirectStandardOutput,
        [string]$RedirectStandardError,
        [string]$RedirectStandardInput,
        [int]$TimeoutSeconds = 0
    )

    $processInfo = [System.Diagnostics.ProcessStartInfo]::new()
    $processInfo.FileName = "docker"
    $processInfo.UseShellExecute = $false
    $processInfo.WorkingDirectory = (Get-Location).Path
    if ($RedirectStandardOutput) {
        $processInfo.RedirectStandardOutput = $true
    }
    if ($RedirectStandardError) {
        $processInfo.RedirectStandardError = $true
    }
    if ($RedirectStandardInput) {
        $processInfo.RedirectStandardInput = $true
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

$null = New-Item -ItemType Directory -Path $OutDir -Force
$OutDir = (Resolve-Path -LiteralPath $OutDir).Path
$timestamp = [DateTime]::UtcNow.ToString("yyyyMMddTHHmmssZ")
$outputPath = Join-Path $OutDir "postgres_$timestamp.sql"
$manifestPath = "$outputPath.manifest.json"
$stderrPath = [System.IO.Path]::GetTempFileName()
$composeArgs = Build-ComposeArgs -ProjectName $ProjectName -EnvFile $EnvFile -ComposeFile $ComposeFile
$backupCompleted = $false

Push-Location $ApiRoot
try {
    $arguments = $composeArgs + @("exec", "-T", "postgres", "sh", "-lc", 'pg_dump -U $POSTGRES_USER -d $POSTGRES_DB --clean --if-exists')
    $exitCode = Start-DockerProcess -Arguments $arguments -RedirectStandardOutput $outputPath -RedirectStandardError $stderrPath -TimeoutSeconds $TimeoutSeconds
    $stderr = if (Test-Path $stderrPath) { Get-Content -LiteralPath $stderrPath -Raw -ErrorAction SilentlyContinue } else { "" }
    if ($exitCode -ne 0) {
        throw "Backup failed. $stderr"
    }
    if ($stderr) {
        Write-Warning $stderr.Trim()
    }

    $dbInfo = & docker @composeArgs exec -T postgres sh -lc 'printf "%s\t%s" "$POSTGRES_DB" "$POSTGRES_USER"'
    $dbParts = "$dbInfo".Split("`t", 2)
    $fileInfo = Get-Item -LiteralPath $outputPath
    $hash = (Get-FileHash -Algorithm SHA256 -LiteralPath $outputPath).Hash.ToUpperInvariant()
    $manifest = [ordered]@{
        schema_version = 1
        created_at_utc = $timestamp
        backup_path = $outputPath
        size_bytes = $fileInfo.Length
        sha256 = $hash
        compose_project = if ($ProjectName) { $ProjectName } else { "sds-api" }
        env_file = (Resolve-Path -LiteralPath $EnvFile).Path
        compose_file = (Resolve-Path -LiteralPath $ComposeFile).Path
        postgres_db = $dbParts[0]
        postgres_user = if ($dbParts.Count -gt 1) { $dbParts[1] } else { "" }
    }
    $manifest | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $manifestPath -Encoding UTF8
    $backupCompleted = $true
}
finally {
    Pop-Location
    Remove-Item -LiteralPath $stderrPath -Force -ErrorAction SilentlyContinue
    if (-not $backupCompleted) {
        Remove-Item -LiteralPath $outputPath -Force -ErrorAction SilentlyContinue
        Remove-Item -LiteralPath $manifestPath -Force -ErrorAction SilentlyContinue
    }
}

Write-Host "Backup OK: $outputPath"
Write-Host "Manifest OK: $manifestPath"
