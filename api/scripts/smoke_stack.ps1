param(
    [ValidateSet("minimal")]
    [string]$Profile = "minimal",
    [switch]$NoStart,
    [switch]$NoBuild,
    [switch]$Cleanup
)

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ApiRoot = (Resolve-Path (Join-Path $ScriptDir "..")).Path
$RepoRoot = (Resolve-Path (Join-Path $ApiRoot "..\..")).Path
$EnvFile = Join-Path $ApiRoot ".env"

$ApiBaseUrl = if ($env:API_BASE_URL) { $env:API_BASE_URL } else { "http://127.0.0.1:8090" }
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
        Push-Location $ApiRoot
        & docker compose --env-file .env -f compose.yml @Arguments
        if ($LASTEXITCODE -ne 0) {
            throw "docker compose failed: $($Arguments -join ' ')"
        }
    }
    finally {
        Pop-Location
        if ($TemporaryEnvironment) {
            foreach ($entry in $TemporaryEnvironment.GetEnumerator()) {
                [Environment]::SetEnvironmentVariable($entry.Key, $saved[$entry.Key], "Process")
            }
        }
    }
}

function Wait-ForUrl {
    param(
        [Parameter(Mandatory = $true)][string]$Url,
        [Parameter(Mandatory = $true)][string]$Name,
        [int]$TimeoutSeconds = 90
    )

    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    Write-Host "[smoke] Waiting for $Name ($Url) ..."
    while ((Get-Date) -lt $deadline) {
        try {
            $null = Invoke-WebRequest -Uri $Url -Method Get -TimeoutSec 5
            Write-Host "[smoke] $Name is up"
            return
        }
        catch {
            Start-Sleep -Seconds 2
        }
    }

    throw "Timeout waiting for $Name ($Url)"
}

function Invoke-Json {
    param(
        [Parameter(Mandatory = $true)][ValidateSet("GET", "POST")][string]$Method,
        [Parameter(Mandatory = $true)][string]$Url,
        [hashtable]$Headers,
        $Body
    )

    $invokeArgs = @{
        Uri         = $Url
        Method      = $Method
        TimeoutSec  = 30
        ContentType = "application/json"
    }
    if ($Headers) {
        $invokeArgs["Headers"] = $Headers
    }
    if ($null -ne $Body) {
        $invokeArgs["Body"] = ($Body | ConvertTo-Json -Depth 8 -Compress)
    }

    return Invoke-RestMethod @invokeArgs
}

function Resolve-SmokeUnit {
    param([AllowNull()][string]$Unit)

    if (-not $Unit) {
        return $null
    }

    switch ($Unit) {
        "sds:CubicMeter" { return "L" }
        "sds:Tonne" { return "t" }
        "sds:Kilogram" { return "kg" }
        "sds:Liter" { return "L" }
        "sds:KilowattHour" { return "kWh" }
        default { return $Unit }
    }
}

function New-SmokeRandomToken {
    param([int]$ByteCount = 32)

    $bytes = New-Object byte[] $ByteCount
    $generator = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try {
        $generator.GetBytes($bytes)
    }
    finally {
        $generator.Dispose()
    }
    return (($bytes | ForEach-Object { $_.ToString("x2") }) -join "")
}

if (-not (Test-Path $EnvFile)) {
    throw "Missing $EnvFile. Run .\scripts\dev.ps1 Setup first."
}

$adminUser = if ($env:ADMIN_USER) { $env:ADMIN_USER } else { "admin" }
$adminPassword = if ($env:ADMIN_PASSWORD) { $env:ADMIN_PASSWORD } else { Get-DotEnvValue -Key "BOOTSTRAP_ADMIN_PASSWORD" }
if (-not $adminPassword) {
    $adminPassword = "admin123"
}

if (-not $NoStart) {
    $upArgs = @("up", "-d")
    if (-not $NoBuild) {
        $upArgs += "--build"
    }
    Invoke-Compose -Arguments $upArgs
}
else {
    Write-Host "[smoke] Reusing already running stack (-NoStart)"
}

Wait-ForUrl -Url "$ApiBaseUrl/healthz" -Name "API"

$docsHtml = (Invoke-WebRequest -Uri "$ApiBaseUrl/docs" -TimeoutSec 30).Content
if ($docsHtml -notmatch "swagger") {
    throw "/docs did not include the API docs shell"
}

$redocHtml = (Invoke-WebRequest -Uri "$ApiBaseUrl/redoc" -TimeoutSec 30).Content
if ($redocHtml -notmatch "redoc") {
    throw "/redoc did not look like ReDoc"
}

Write-Host "[smoke] Logging in as $adminUser"
$loginResponse = Invoke-Json -Method POST -Url "$ApiBaseUrl/auth/login" -Body @{
    username = $adminUser
    password = $adminPassword
}
$token = $loginResponse.access_token
if (-not $token) {
    throw "Login failed (no access_token returned)"
}

$headers = @{ Authorization = "Bearer $token" }

Write-Host "[smoke] Checking authenticated API readiness"
$null = Invoke-Json -Method GET -Url "$ApiBaseUrl/ready" -Headers $headers

Write-Host "[smoke] Checking semantic concept catalogue"
$indicatorResponse = Invoke-Json -Method GET -Url "$ApiBaseUrl/api/v1/indicators?limit=1" -Headers $headers
$conceptResponse = Invoke-Json -Method GET -Url "$ApiBaseUrl/api/v1/concepts?limit=50" -Headers $headers
$minSemanticConcepts = if ($env:MIN_SEMANTIC_CONCEPTS) { [int]$env:MIN_SEMANTIC_CONCEPTS } else { 8 }
if ($null -eq $conceptResponse.total -or [int]$conceptResponse.total -lt $minSemanticConcepts) {
    throw "/api/v1/concepts returned total=$($conceptResponse.total); expected at least $minSemanticConcepts. Run the semantic backfill/refresh before accepting the stack."
}
if ($null -ne $indicatorResponse.total -and [int]$conceptResponse.total -lt [int]$indicatorResponse.total) {
    throw "/api/v1/concepts returned total=$($conceptResponse.total); expected at least active indicator total=$($indicatorResponse.total). Run the semantic catalog projector before accepting the stack."
}

Write-Host "[smoke] Fetching one compatible concept"
$concept = $null
$conceptUnit = $null
foreach ($candidate in @($conceptResponse.items)) {
    if (-not $candidate.uri) {
        continue
    }

    $resolvedUnit = Resolve-SmokeUnit -Unit $candidate.unit
    if (-not $candidate.unit -or ($resolvedUnit -and $resolvedUnit -notmatch ":")) {
        $concept = $candidate
        $conceptUnit = if ($resolvedUnit) { $resolvedUnit } else { "L" }
        break
    }
}

if (-not $concept -or -not $concept.uri) {
    throw "No compatible concept returned by /api/v1/concepts"
}
$entityId = "smoke_facility"

Write-Host "[smoke] Creating hierarchy"
$hierarchyResponse = Invoke-Json -Method POST -Url "$ApiBaseUrl/api/v1/hierarchies" -Headers $headers -Body @{
    company_id     = "smoke_company"
    hierarchy_type = "organizational"
    name           = "smoke"
    description    = "smoke config"
    levels         = @(
        @{ id = "root"; name = "Root"; parent = $null; level = 0 },
        @{ id = $entityId; name = "Smoke Facility"; parent = "root"; level = 1 }
    )
    active         = $true
}
$hierarchyId = $hierarchyResponse.id
if (-not $hierarchyId) {
    throw "Failed to create hierarchy config"
}

$null = Invoke-Json -Method POST -Url "$ApiBaseUrl/api/v1/hierarchies/$hierarchyId/activate" -Headers $headers

$smokeIdentitySuffix = New-SmokeRandomToken -ByteCount 15
$smokeUsername = "smoke_data_manager_$smokeIdentitySuffix"
$smokeEmail = "smoke-$smokeIdentitySuffix@example.com"
$smokePassword = New-SmokeRandomToken -ByteCount 32

Write-Host "[smoke] Creating a disposable tenant-bound data manager"
$null = Invoke-Json -Method POST -Url "$ApiBaseUrl/auth/users" -Headers $headers -Body @{
    username   = $smokeUsername
    password   = $smokePassword
    email      = $smokeEmail
    full_name  = "Disposable smoke data manager"
    company_id = "smoke_company"
    role       = "data_manager"
}

$dataManagerLogin = Invoke-Json -Method POST -Url "$ApiBaseUrl/auth/login" -Body @{
    username = $smokeUsername
    password = $smokePassword
}
$dataManagerToken = $dataManagerLogin.access_token
if (-not $dataManagerToken) {
    throw "Disposable data-manager login failed"
}
$dataManagerHeaders = @{ Authorization = "Bearer $dataManagerToken" }

Write-Host "[smoke] Creating value"
$valueResponse = Invoke-Json -Method POST -Url "$ApiBaseUrl/api/v1/values" -Headers $dataManagerHeaders -Body @{
    concept = $concept.uri
    entity  = $entityId
    period  = "2024-01-15"
    value   = 123
    unit    = $conceptUnit
}
$valueId = $valueResponse.id
if (-not $valueId) {
    throw "Failed to create value"
}

$valuesResponse = Invoke-Json -Method GET -Url "$ApiBaseUrl/api/v1/values?entity=$entityId&limit=50" -Headers $dataManagerHeaders
$valueIds = @($valuesResponse.items | ForEach-Object { $_.id })
if ($valueIds -notcontains $valueId) {
    throw "Created value was not returned by /api/v1/values"
}

$null = Invoke-Json -Method GET -Url "$ApiBaseUrl/api/v1/calculate/dependencies/csrd:E3_5" -Headers $dataManagerHeaders

Write-Host "[smoke] Bulk importing smoke values"
$importResult = Invoke-Json -Method POST -Url "$ApiBaseUrl/api/v1/values/import" -Headers $dataManagerHeaders -Body @{
    items = @(
        @{
            concept       = $concept.uri
            entity        = $entityId
            period        = "2024-01-16"
            period_start  = "2024-01-16"
            period_end    = "2024-01-16"
            external_key  = "smoke:${hierarchyId}:bulk-1"
            value         = 124
            value_type    = "numeric"
            unit          = $conceptUnit
            metadata      = @{ source = "smoke_stack" }
        },
        @{
            concept       = $concept.uri
            entity        = $entityId
            period        = "2024-01-17"
            period_start  = "2024-01-17"
            period_end    = "2024-01-17"
            external_key  = "smoke:${hierarchyId}:bulk-2"
            value         = 125
            value_type    = "numeric"
            unit          = $conceptUnit
            metadata      = @{ source = "smoke_stack" }
        }
    )
}
if (-not $importResult.committed) {
    throw "Smoke value import did not commit"
}

Write-Host "[smoke] Smoke test OK (profile=$Profile)"

if ($Cleanup) {
    Write-Host "[smoke] Cleanup requested"
    Invoke-Compose -Arguments @("down", "--remove-orphans")
}
