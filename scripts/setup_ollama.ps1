# Legacy Windows entry point. The portable installer is scripts/project.py.
$ErrorActionPreference = 'Stop'
Push-Location (Split-Path -Parent $PSScriptRoot)
try {
    & python -m scripts.project ollama
    if ($LASTEXITCODE -ne 0) {
        throw "Ollama setup failed with exit code $LASTEXITCODE."
    }
}
finally {
    Pop-Location
}
