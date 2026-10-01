# Abre o Ad Animado: sobe o servidor se ainda nao estiver rodando e abre o navegador.
# E o que o atalho da area de trabalho chama.
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
$Porta = if ($env:PORTA) { $env:PORTA } else { "4124" }
$Log = Join-Path $env:USERPROFILE ".ad-animado.log"
$env:PYTHONUTF8 = "1"

function Respondendo {
  try { return (Invoke-WebRequest -Uri "http://127.0.0.1:$Porta/api/health" -UseBasicParsing -TimeoutSec 2).StatusCode -eq 200 }
  catch { return $false }
}

if (-not (Respondendo)) {
  $VenvPython = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
  Start-Process -FilePath $VenvPython -ArgumentList "-X", "utf8", "app\servidor.py" -WorkingDirectory $PSScriptRoot `
    -WindowStyle Hidden -RedirectStandardOutput $Log -RedirectStandardError "$Log.err"
  for ($i = 0; $i -lt 30; $i++) { if (Respondendo) { break }; Start-Sleep -Seconds 1 }
}
Start-Process "http://localhost:$Porta"
