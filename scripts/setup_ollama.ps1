# Install the runtime without starting services or downloading models.

$ErrorActionPreference = 'Stop'
$command = Get-Command ollama -CommandType Application -ErrorAction SilentlyContinue
$installDirectory = Join-Path $env:LOCALAPPDATA 'Programs\Ollama'
$executable = Join-Path $installDirectory 'ollama.exe'

if ($command -or (Test-Path -LiteralPath $executable -PathType Leaf)) {
    Write-Host 'Ollama is already installed. No services were started.'
    return
}

# The standalone archive has no GUI installer or automatic startup behavior.
$archiveName = 'ollama-runtime-{0}.zip' -f [guid]::NewGuid()
$archivePath = Join-Path ([IO.Path]::GetTempPath()) $archiveName
$downloadUrl = 'https://github.com/ollama/ollama/releases/latest/download/ollama-windows-amd64.zip'
Write-Host 'Downloading the standalone Ollama runtime...'
Invoke-WebRequest -Uri $downloadUrl -OutFile $archivePath -UseBasicParsing
New-Item -ItemType Directory -Path $installDirectory -Force | Out-Null
Expand-Archive -LiteralPath $archivePath -DestinationPath $installDirectory -Force
if (-not (Test-Path -LiteralPath $executable -PathType Leaf)) {
    throw "Ollama extraction failed. Archive retained at: $archivePath"
}
Write-Host "Ollama installed at: $installDirectory"
Write-Host 'No server or model was started. Use make run to start the project.'
