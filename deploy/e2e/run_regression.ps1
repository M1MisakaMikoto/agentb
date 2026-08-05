param(
    [ValidateRange(1, 65535)]
    [int]$Port = 8152,

    [ValidateSet("standalone", "platform")]
    [string]$ComposeMode = "standalone",

    [string]$ConfigPath = "WorkBranch/backend/.test/test_config.yaml"
)

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "../..")).Path
$timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
$logDir = Join-Path $repoRoot "WorkBranch/backend/.test/logs/distributed_$timestamp"
New-Item -ItemType Directory -Path $logDir -Force | Out-Null

$venvPython = Join-Path $repoRoot ".venv/Scripts/python.exe"
if (Test-Path -LiteralPath $venvPython) {
    $python = $venvPython
} else {
    $pythonCommand = Get-Command python -ErrorAction Stop
    $python = $pythonCommand.Source
}

$routerBaseUrl = "http://127.0.0.1:$Port"
$env:AGENTB_E2E_BASE_URL = $routerBaseUrl
$env:AGENTB_E2E_API_BASE_URL = "$routerBaseUrl/api"

$smokeLog = Join-Path $logDir "affinity_smoke.log"
$runnerLog = Join-Path $logDir "distributed_regression.log"
$composeLog = Join-Path $logDir "compose.log"
$summaryLog = Join-Path $logDir "summary.txt"

"Affinity smoke did not start." | Set-Content -LiteralPath $smokeLog -Encoding utf8
"Distributed regression did not start." | Set-Content -LiteralPath $runnerLog -Encoding utf8
"Compose log collection did not start." | Set-Content -LiteralPath $composeLog -Encoding utf8
"Regression summary is pending." | Set-Content -LiteralPath $summaryLog -Encoding utf8

$smokeExit = 1
$regressionExit = 1
$dependencyExit = 1
$composeExit = 1

Push-Location $repoRoot
try {
    $ErrorActionPreference = "Continue"
    $dependencyOutput = & $python -c "import httpx, yaml" 2>&1
    $dependencyExit = $LASTEXITCODE
    $ErrorActionPreference = "Stop"
    if ($dependencyExit -ne 0) {
        @(
            "E2E dependency check failed. Install requirements.txt before running."
            $dependencyOutput
        ) | Set-Content -LiteralPath $runnerLog -Encoding utf8
        $regressionExit = 3
    } else {
        $ErrorActionPreference = "Continue"
        & $python "WorkBranch/backend/.test/run_e2e_tests.py" `
            --no-server `
            --suite distributed_regression `
            --config $ConfigPath `
            --preflight-only `
            --output $runnerLog
        $regressionExit = $LASTEXITCODE
        $ErrorActionPreference = "Stop"
    }

    if ($regressionExit -eq 0) {
        Invoke-WebRequest -UseBasicParsing -Uri "$routerBaseUrl/router-health" -TimeoutSec 10 | Out-Null

        $ErrorActionPreference = "Continue"
        & $python "deploy/e2e/affinity_smoke.py" 2>&1 |
            Tee-Object -FilePath $smokeLog
        $smokeExit = $LASTEXITCODE
        $ErrorActionPreference = "Stop"

        $ErrorActionPreference = "Continue"
        & $python "WorkBranch/backend/.test/run_e2e_tests.py" `
            --no-server `
            --suite distributed_regression `
            --config $ConfigPath `
            --verbose `
            --output $runnerLog
        $regressionExit = $LASTEXITCODE
        $ErrorActionPreference = "Stop"
    } else {
        "Affinity smoke skipped because dependency or fixture preflight failed." |
            Set-Content -LiteralPath $smokeLog -Encoding utf8
    }
} finally {
    $ErrorActionPreference = "Stop"
    $composeArgs = @()
    $composeEnv = Join-Path $repoRoot ".env.compose"
    if (Test-Path -LiteralPath $composeEnv) {
        $composeArgs += @("--env-file", $composeEnv)
    }
    $composeArgs += @("-f", "compose.yml")
    if ($ComposeMode -eq "standalone") {
        $composeArgs += @("-f", "compose.standalone.yml")
    } else {
        $composeArgs += @("-f", "compose.platform.yml")
    }

    try {
        $dockerCommand = Get-Command docker -ErrorAction SilentlyContinue
        if ($dockerCommand) {
            $composeOutput = @(
                & $dockerCommand.Source compose @composeArgs logs --no-color 2>&1
            )
            $composeExit = $LASTEXITCODE
        } elseif (Get-Command wsl.exe -ErrorAction SilentlyContinue) {
            $composeOutput = @(
                & wsl.exe --cd $repoRoot docker compose @composeArgs logs --no-color 2>&1
            )
            $composeExit = $LASTEXITCODE
        } else {
            throw "Neither Docker CLI nor WSL is available"
        }

        if ($composeOutput.Count -eq 0) {
            $composeOutput = @("Compose log command completed with no output.")
        }
        if ($composeExit -ne 0) {
            $composeOutput += "Compose log command exited with code $composeExit."
        }
        $composeOutput | Set-Content -LiteralPath $composeLog -Encoding utf8
    } catch {
        $composeExit = 1
        "Compose log collection failed: $($_.Exception.Message)" |
            Set-Content -LiteralPath $composeLog -Encoding utf8
    }

    @(
        "router=$routerBaseUrl"
        "dependency_exit=$dependencyExit"
        "affinity_smoke_exit=$smokeExit"
        "distributed_regression_exit=$regressionExit"
        "compose_log_exit=$composeExit"
        "logs=$logDir"
    ) | Set-Content -LiteralPath $summaryLog -Encoding utf8

    Pop-Location
}

Write-Host "Regression logs: $logDir"
if ($smokeExit -ne 0 -or $regressionExit -ne 0) {
    exit 1
}
exit 0
