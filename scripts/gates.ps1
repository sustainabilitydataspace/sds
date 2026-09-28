param(
    [ValidateSet("Help", "Status", "RepoClosureCheck", "AtomizerSync", "AtomizerBoundaryCheck", "E1Gate", "E2Gate", "E2GateDetection", "E3ExportNgsi", "SemanticsBundle", "E4Gate", "GatePipeline", "E5Gate", "GateSemantics", "E6RefreshEdc", "E6Check", "E6Gate", "GateGovernance", "E6Evidence", "E8Gate", "E9Gate", "E10Gate", "E11Gate", "E11DeployGate", "ServiceWave4", "ServiceWave5", "ServiceTest", "ServiceCoverage")]
    [string]$Command = "Help",
    [string]$SourceDir
)

$ScriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Split-Path -Parent $ScriptRoot
$ClosureCheck = Join-Path $RepoRoot "scripts\repo_closure_check.py"
$RootPython = Join-Path $RepoRoot "api\.venv\Scripts\python.exe"
$SemanticsGate = Join-Path $RepoRoot "scripts\run_semantics_gate.py"
$E6Gate = Join-Path $RepoRoot "scripts\run_e6_gate.py"
$ServiceScript = Join-Path $RepoRoot "api\scripts\dev.ps1"
$StatusFile = Join-Path $RepoRoot "deliverables\README.md"

function Show-Help {
    Write-Host "SustainabilityDataSpace root gates"
    Write-Host ""
    Write-Host "  .\scripts\gates.ps1 Status"
    Write-Host "  .\scripts\gates.ps1 RepoClosureCheck"
    Write-Host "  .\scripts\gates.ps1 AtomizerSync [-SourceDir <path>]"
    Write-Host "  .\scripts\gates.ps1 AtomizerBoundaryCheck"
    Write-Host "  .\scripts\gates.ps1 E1Gate"
    Write-Host "  .\scripts\gates.ps1 E2Gate"
    Write-Host "  .\scripts\gates.ps1 E2GateDetection"
    Write-Host "  .\scripts\gates.ps1 E3ExportNgsi"
    Write-Host "  .\scripts\gates.ps1 SemanticsBundle"
    Write-Host "  .\scripts\gates.ps1 E4Gate"
    Write-Host "  .\scripts\gates.ps1 GatePipeline"
    Write-Host "  .\scripts\gates.ps1 E5Gate"
    Write-Host "  .\scripts\gates.ps1 GateSemantics"
    Write-Host "  .\scripts\gates.ps1 E6RefreshEdc"
    Write-Host "  .\scripts\gates.ps1 E6Check"
    Write-Host "  .\scripts\gates.ps1 E6Gate"
    Write-Host "  .\scripts\gates.ps1 GateGovernance"
    Write-Host "  .\scripts\gates.ps1 E6Evidence"
    Write-Host "  .\scripts\gates.ps1 E8Gate"
    Write-Host "  .\scripts\gates.ps1 E9Gate"
    Write-Host "  .\scripts\gates.ps1 E10Gate"
    Write-Host "  .\scripts\gates.ps1 E11Gate"
    Write-Host "  .\scripts\gates.ps1 E11DeployGate"
    Write-Host "  .\scripts\gates.ps1 ServiceWave4"
    Write-Host "  .\scripts\gates.ps1 ServiceWave5"
    Write-Host "  .\scripts\gates.ps1 ServiceTest"
    Write-Host "  .\scripts\gates.ps1 ServiceCoverage"
}

function Invoke-RepoClosureCheck {
    & python $ClosureCheck
    exit $LASTEXITCODE
}

function Invoke-RootPython {
    param(
        [Parameter(ValueFromRemainingArguments = $true)]
        [string[]]$Arguments
    )

    if (-not (Test-Path $RootPython)) {
        Write-Error "Root Python not found: $RootPython"
        exit 1
    }

    & $RootPython @Arguments
    exit $LASTEXITCODE
}

switch ($Command) {
    "Help" {
        Show-Help
    }
    "Status" {
        Write-Host $StatusFile
        Write-Host ""
        Invoke-RepoClosureCheck
    }
    "RepoClosureCheck" {
        Invoke-RepoClosureCheck
    }
    "AtomizerSync" {
        $arguments = @("scripts\sync_atomizer_exports.py")
        if ($SourceDir) {
            $arguments += @("--source-dir", $SourceDir)
        }
        Invoke-RootPython @arguments
    }
    "AtomizerBoundaryCheck" {
        Invoke-RootPython "scripts\check_dependency_boundaries.py"
    }
    "E1Gate" {
        Invoke-RootPython "scripts\e1_check_inventory.py" "--strict"
    }
    "E2Gate" {
        Invoke-RootPython "scripts\e2_gate.py" "--strict"
    }
    "E2GateDetection" {
        Invoke-RootPython "scripts\e2_gate_detection.py" "--strict"
    }
    "E3ExportNgsi" {
        Invoke-RootPython "scripts\e3_export_ngsi_ld.py" "--context-mode" "embed"
    }
    "SemanticsBundle" {
        Invoke-RootPython "scripts\semantics_bundle_build.py"
    }
    "E4Gate" {
        Invoke-RootPython "scripts\e4_gate.py"
    }
    "GatePipeline" {
        Invoke-RootPython "scripts\e4_gate.py"
    }
    "E5Gate" {
        Invoke-RootPython "-m" "pytest" "api\tests\test_e5_technical_code_pack.py" "-q"
    }
    "GateSemantics" {
        Invoke-RootPython "scripts\run_semantics_gate.py"
    }
    "E6RefreshEdc" {
        Invoke-RootPython "scripts\edc_bundle_from_register.py" "--strict"
    }
    "E6Check" {
        Invoke-RootPython "scripts\e6_check_governance.py"
    }
    "E6Gate" {
        Invoke-RootPython "scripts\run_e6_gate.py"
    }
    "GateGovernance" {
        Invoke-RootPython "scripts\run_e6_gate.py"
    }
    "E6Evidence" {
        Invoke-RootPython "scripts\e6_generate_evidence.py"
    }
    "E8Gate" {
        Invoke-RootPython "-m" "pytest" "api\tests\test_e8_use_cases_and_prioritization.py" "-q"
    }
    "E9Gate" {
        Invoke-RootPython "-m" "pytest" "api\tests\test_e9_adjustments_report.py" "-q"
    }
    "E10Gate" {
        Invoke-RootPython "-m" "pytest" "api\tests\test_e10_comms_plan.py" "-q"
    }
    "E11Gate" {
        Invoke-RootPython "-m" "pytest" "api\tests\test_e11_website_pack.py" "-q"
    }
    "E11DeployGate" {
        Invoke-RootPython "-m" "pytest" "api\tests\test_e11_website_pack.py" "-q"
    }
    "ServiceWave4" {
        Push-Location (Join-Path $RepoRoot "api")
        try {
            & make gate-w4
            exit $LASTEXITCODE
        }
        finally {
            Pop-Location
        }
    }
    "ServiceWave5" {
        Push-Location (Join-Path $RepoRoot "api")
        try {
            & make gate-w5
            exit $LASTEXITCODE
        }
        finally {
            Pop-Location
        }
    }
    "ServiceTest" {
        & $ServiceScript Test
        exit $LASTEXITCODE
    }
    "ServiceCoverage" {
        & $ServiceScript TestCoverage
        exit $LASTEXITCODE
    }
}
