[CmdletBinding()]
param(
    [string]$EnvironmentName = "butterfly-qa",
    [switch]$WithDevDependencies
)

$ErrorActionPreference = "Stop"
$env:PYTHONUTF8 = "1"
$ProjectRoot = $PSScriptRoot

function Invoke-Checked {
    param(
        [Parameter(Mandatory = $true)]
        [scriptblock]$Command,
        [Parameter(Mandatory = $true)]
        [string]$FailureMessage
    )

    & $Command
    if ($LASTEXITCODE -ne 0) {
        throw $FailureMessage
    }
}

if (-not (Get-Command conda -ErrorAction SilentlyContinue)) {
    throw "Conda was not found. Install Miniconda or Anaconda, then reopen PowerShell."
}

$environmentListJson = conda env list --json | Out-String
if ($LASTEXITCODE -ne 0) {
    throw "Unable to read the Conda environment list."
}
$environmentList = $environmentListJson | ConvertFrom-Json
$environmentExists = $environmentList.envs | Where-Object {
    (Split-Path $_ -Leaf) -eq $EnvironmentName
}

if (-not $environmentExists) {
    Write-Host "Creating Conda environment: $EnvironmentName"
    Invoke-Checked {
        conda create -n $EnvironmentName python=3.10 -y
    } "Failed to create Conda environment: $EnvironmentName"
}
else {
    Write-Host "Using existing Conda environment: $EnvironmentName"
}

Invoke-Checked {
    conda run -n $EnvironmentName python -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)"
} "Python 3.10 or newer is required."

Write-Host "Persisting PYTHONUTF8=1 for the Conda environment"
Invoke-Checked {
    conda env config vars set -n $EnvironmentName PYTHONUTF8=1
} "Failed to persist PYTHONUTF8 for the Conda environment."

Push-Location $ProjectRoot
try {
    $packageSpec = if ($WithDevDependencies) { ".[dev]" } else { "." }
    Write-Host "Installing Butterfly QA: $packageSpec"
    Invoke-Checked {
        conda run -n $EnvironmentName python -m pip install --upgrade $packageSpec
    } "Butterfly QA installation failed. Check network access, pip output, and directory permissions."

    Invoke-Checked {
        conda run -n $EnvironmentName python -c "from pathlib import Path; import qa_agent.cli, qa_agent.web; static = Path(qa_agent.web.__file__).parent / 'static' / 'index.html'; raise SystemExit(0 if static.is_file() else 1)"
    } "Installation verification failed. The CLI or Web static files were not found."
}
finally {
    Pop-Location
}

Write-Host ""
Write-Host "Butterfly QA installation completed."
Write-Host "Next steps:"
Write-Host "  conda activate $EnvironmentName"
Write-Host "  cd `"$ProjectRoot`""
Write-Host "  butterfly-qa web"
