param(
    [ValidateSet("unit", "mock", "h5ad", "10x", "all")]
    [string]$Suite = "unit",

    [string]$H5ad = "",
    [string]$MatrixDir = "",
    [string[]]$Metadata = @(),
    [string]$MetadataBarcodeColumn = "",

    [string]$Model = $env:DEEPSEEK_MODEL,
    [string]$BaseUrl = $env:DEEPSEEK_BASE_URL,
    [string]$Root = ".smoke_runs",
    [string]$ProjectId = "",
    [int]$MaxSteps = 12,
    [int]$MaxOutputTokens = 8192,
    [int]$ContextMaxChars = 64000,
    [double]$Temperature = 0.0,
    [string]$Goal = "",

    [switch]$AllowInsecureExecution,
    [switch]$PrintEvents,
    [switch]$FriendlyStream,
    [switch]$InteractiveAnswer,
    [switch]$List
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

if ([string]::IsNullOrWhiteSpace($Model)) {
    $Model = "deepseek-v4-pro"
}
if ([string]::IsNullOrWhiteSpace($BaseUrl)) {
    $BaseUrl = "https://api.deepseek.com"
}

function Write-Section {
    param([string]$Text)
    Write-Host ""
    Write-Host "=== $Text ===" -ForegroundColor Cyan
}

function Require-ApiKey {
    if ([string]::IsNullOrWhiteSpace($env:DEEPSEEK_API_KEY)) {
        throw "DEEPSEEK_API_KEY is not set. In PowerShell run: `$env:DEEPSEEK_API_KEY='...'"
    }
}

function Invoke-Checked {
    param([string[]]$Command)
    Write-Host ""
    Write-Host ("PS> " + ($Command -join " ")) -ForegroundColor DarkGray
    & $Command[0] @($Command[1..($Command.Length - 1)])
    if ($LASTEXITCODE -ne 0) {
        throw "Command failed with exit code $LASTEXITCODE"
    }
}

function Common-Args {
    $args = @(
        "--model", $Model,
        "--base-url", $BaseUrl,
        "--root", $Root,
        "--max-steps", [string]$MaxSteps,
        "--max-output-tokens", [string]$MaxOutputTokens,
        "--context-max-chars", [string]$ContextMaxChars,
        "--temperature", [string]$Temperature
    )
    if (-not [string]::IsNullOrWhiteSpace($ProjectId)) {
        $args += @("--project-id", $ProjectId)
    }
    if (-not [string]::IsNullOrWhiteSpace($Goal)) {
        $args += @("--goal", $Goal)
    }
    if ($AllowInsecureExecution) {
        $args += "--allow-insecure-execution"
    }
    if ($PrintEvents) {
        $args += "--print-events"
    }
    if ($InteractiveAnswer) {
        $args += "--interactive-answer"
    }
    return $args
}

function Run-Unit {
    Write-Section "Unit/runtime regression"
    Invoke-Checked -Command @("python", "-m", "pytest", "-q")
}

function Run-Mock {
    Require-ApiKey
    Write-Section "DeepSeek fake-data provider contract smoke"
    $args = @(
        "scripts\deepseek_smoke.py",
        "--model", $Model,
        "--base-url", $BaseUrl,
        "--root", $Root,
        "--max-steps", [string]$MaxSteps,
        "--max-output-tokens", [string]$MaxOutputTokens,
        "--context-max-chars", [string]$ContextMaxChars,
        "--temperature", [string]$Temperature
    )
    if (-not [string]::IsNullOrWhiteSpace($ProjectId)) {
        $args += @("--project-id", $ProjectId)
    }
    if (-not [string]::IsNullOrWhiteSpace($Goal)) {
        $args += @("--goal", $Goal)
    }
    if ($PrintEvents) {
        $args += "--print-events"
    }
    Invoke-Checked -Command (@("python") + $args)
}

function Run-H5ad {
    Require-ApiKey
    if ([string]::IsNullOrWhiteSpace($H5ad)) {
        throw "-H5ad is required for -Suite h5ad or -Suite all"
    }
    if (-not (Test-Path -LiteralPath $H5ad)) {
        throw "h5ad path not found: $H5ad"
    }
    Write-Section "DeepSeek real h5ad smoke"
    $args = @("scripts\deepseek_h5ad_smoke.py", "--h5ad", $H5ad) + (Common-Args)
    Invoke-Checked -Command (@("python") + $args)
}

function Run-10x {
    Require-ApiKey
    if ([string]::IsNullOrWhiteSpace($MatrixDir)) {
        throw "-MatrixDir is required for -Suite 10x or -Suite all"
    }
    if (-not (Test-Path -LiteralPath $MatrixDir)) {
        throw "10x matrix directory not found: $MatrixDir"
    }
    Write-Section "DeepSeek real 10x project smoke"
    $args = @("scripts\deepseek_10x_smoke.py", "--matrix-dir", $MatrixDir) + (Common-Args)
    foreach ($item in $Metadata) {
        if (-not (Test-Path -LiteralPath $item)) {
            throw "metadata path not found: $item"
        }
        $args += @("--metadata", $item)
    }
    if (-not [string]::IsNullOrWhiteSpace($MetadataBarcodeColumn)) {
        $args += @("--metadata-barcode-column", $MetadataBarcodeColumn)
    }
    if ($FriendlyStream) {
        $args += "--friendly-stream"
    }
    Invoke-Checked -Command (@("python") + $args)
}

if ($List) {
    Write-Host "Available suites:"
    Write-Host "  unit  : pytest regression, no API key required"
    Write-Host "  mock  : DeepSeek provider contract smoke on fake data"
    Write-Host "  h5ad  : DeepSeek smoke on a real .h5ad file, requires -H5ad"
    Write-Host "  10x   : DeepSeek smoke on raw 10x matrix dir, requires -MatrixDir"
    Write-Host "  all   : unit + mock + h5ad + 10x"
    exit 0
}

Write-Host "Pertura smoke suite"
Write-Host "suite=$Suite model=$Model base_url=$BaseUrl root=$Root max_steps=$MaxSteps max_output_tokens=$MaxOutputTokens context_max_chars=$ContextMaxChars"

switch ($Suite) {
    "unit" { Run-Unit }
    "mock" { Run-Mock }
    "h5ad" { Run-H5ad }
    "10x" { Run-10x }
    "all" {
        Run-Unit
        Run-Mock
        Run-H5ad
        Run-10x
    }
}


