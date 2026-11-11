# Run from a Windows desktop agent. Native GUI checks need an unlocked desktop.
[CmdletBinding()]
param(
    [string]$Output = "output/m0/windows",
    [switch]$Install,
    [switch]$SkipNativeGui,
    [switch]$SkipLiveObservation
)
$ErrorActionPreference = "Stop"
$repoPath = Split-Path -Parent $PSScriptRoot
Set-Location $repoPath
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    throw "uv is required. Install uv and Python 3.12, then rerun this script."
}
if ($env:QT_QPA_PLATFORM -in @("offscreen", "minimal") -and (-not $SkipNativeGui -or -not $SkipLiveObservation)) {
    throw "Native acceptance requires an unlocked native desktop. Unset QT_QPA_PLATFORM before rerunning."
}
New-Item -ItemType Directory -Force -Path $Output | Out-Null
$checkResults = [System.Collections.Generic.List[object]]::new()
function Invoke-M0Check {
    param([string]$Name, [string[]]$Arguments)
    $logPath = Join-Path $Output ($Name + ".log")
    & uv @Arguments *> $logPath
    $processCode = $LASTEXITCODE
    $checkResults.Add([pscustomobject]@{ name=$Name; exit_code=$processCode; log=$logPath; status=$(if ($processCode -eq 0) {"passed"} else {"failed"}) })
    Write-Host "$Name : exit $processCode ($logPath)"
}
if ($Install) {
    & uv sync --python 3.12 --extra dev --locked
    if ($LASTEXITCODE -ne 0) { throw "uv sync failed; no acceptance result can be claimed." }
}
Invoke-M0Check -Name "examples" -Arguments @("run", "--offline", "python", "-m", "deskpet.m0", "examples", "--check")
Invoke-M0Check -Name "observation" -Arguments @("run", "--offline", "python", "-m", "deskpet.m0", "observe", "--input", "docs/contracts/raw-example.json", "--output", (Join-Path $Output "observation"))
# Tests use subprocess-isolated offscreen GUI checks. Native processes below do not inherit this temporary override.
$previousQtPlatform = $env:QT_QPA_PLATFORM
try {
    $env:QT_QPA_PLATFORM = "offscreen"
    Invoke-M0Check -Name "pytest" -Arguments @("run", "--offline", "python", "-m", "pytest", "-q", "tests")
} finally {
    if ($null -eq $previousQtPlatform) { Remove-Item Env:QT_QPA_PLATFORM -ErrorAction SilentlyContinue }
    else { $env:QT_QPA_PLATFORM = $previousQtPlatform }
}
Invoke-M0Check -Name "replay" -Arguments @("run", "--offline", "python", "-m", "deskpet.m0", "replay", "--suite", "m0", "--output", (Join-Path $Output "replay"))
if (-not $SkipNativeGui) {
    Invoke-M0Check -Name "native-gui" -Arguments @("run", "--offline", "python", "-m", "deskpet.m0", "gui", "--profile", "scenario", "--auto-smoke", "--output", (Join-Path $Output "native-gui"))
} else { $checkResults.Add([pscustomobject]@{name="native-gui";status="not_run";exit_code=$null}) }
if (-not $SkipLiveObservation) {
    Invoke-M0Check -Name "live-observation" -Arguments @("run", "--offline", "python", "-m", "deskpet.m0", "smoke", "--profile", "live-smoke", "--output", (Join-Path $Output "live-observation"))
} else { $checkResults.Add([pscustomobject]@{name="live-observation";status="not_run";exit_code=$null}) }
$allPassed = @($checkResults | Where-Object { $_.status -ne "passed" }).Count -eq 0
$summary = [ordered]@{
    status = $(if ($allPassed) {"automated_checks_passed_manual_review_pending"} else {"incomplete_or_failed"})
    platform = "Windows"
    checks = $checkResults.ToArray()
    manual_review = "pending"
    manual_checklist = @("输入与按钮可用", "提示卡可读", "后台刷新不抢焦点", "取消后卡片不重开")
    instructions = "Return this directory, native-gui-report.json, screenshots and manual observations. Compare source_sha256 with the macOS report."
}
$summary | ConvertTo-Json -Depth 8 | Set-Content -Encoding UTF8 (Join-Path $Output "windows-summary.json")
Write-Host "Return the complete $Output directory and describe manual checks."
if (-not $allPassed) { exit 1 }
