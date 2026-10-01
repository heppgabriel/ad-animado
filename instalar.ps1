# Ad Animado - instalacao no Windows. Um comando so, no PowerShell:
#
#   irm https://raw.githubusercontent.com/heppgabriel/ad-animado/main/instalar.ps1 | iex
#
# Instala o que faltar (git, Python 3.12, ffmpeg) pelo winget, baixa o app em
# %USERPROFILE%\Ad Animado, monta o ambiente Python, cria um atalho na area de
# trabalho e abre o app no navegador. Pode rodar de novo para atualizar: os ads
# (pasta "Ads Animados") e as chaves (.config\ad-animado) nunca sao apagados.
# Mensagens sem acento de proposito: o console do Windows as vezes mostra UTF-8 errado.

$ErrorActionPreference = "Stop"
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch {}

$RepoUrl = if ($env:AD_ANIMADO_REPO) { $env:AD_ANIMADO_REPO } else { "https://github.com/heppgabriel/ad-animado.git" }
$Dest = if ($env:AD_ANIMADO_DEST) { $env:AD_ANIMADO_DEST } else { Join-Path $env:USERPROFILE "Ad Animado" }
$Porta = if ($env:PORTA) { $env:PORTA } else { "4124" }

Write-Host "== Ad Animado - instalacao (Windows) =="
Write-Host ""

function Atualizar-Path {
  $env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [Environment]::GetEnvironmentVariable("Path", "User")
}
function Achar-Python312 {
  if (Get-Command py -ErrorAction SilentlyContinue) {
    $exe = & py -3.12 -c "import sys; print(sys.executable)" 2>$null
    if ($LASTEXITCODE -eq 0 -and $exe) { return $exe.Trim() }
  }
  $cmd = Get-Command python -ErrorAction SilentlyContinue
  if ($cmd) {
    $ver = & python -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null
    if ($ver -eq "3.12") { return $cmd.Source }
  }
  return $null
}

# 1. dependencias
$falta = @()
if (-not (Get-Command git -ErrorAction SilentlyContinue)) { $falta += "Git.Git" }
if (-not (Get-Command ffmpeg -ErrorAction SilentlyContinue)) { $falta += "Gyan.FFmpeg" }
$PythonExe = Achar-Python312
if (-not $PythonExe) { $falta += "Python.Python.3.12" }
if ($falta.Count -gt 0) {
  if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
    Write-Host "Faltam: $($falta -join ', ') e nao achei o winget."
    Write-Host "Instale o 'Instalador de Aplicativo' pela Microsoft Store (traz o winget) e rode este comando de novo."
    exit 1
  }
  Write-Host "Instalando com o winget: $($falta -join ', ')..."
  foreach ($pacote in $falta) { winget install --id $pacote -e --silent --accept-package-agreements --accept-source-agreements }
  Atualizar-Path
  if (-not $PythonExe) { $PythonExe = Achar-Python312 }
}
if (-not $PythonExe -or -not (Get-Command git -ErrorAction SilentlyContinue)) {
  Write-Host ""
  Write-Host "Acabei de instalar o que faltava, mas esta janela ainda nao enxerga. Feche o PowerShell,"
  Write-Host "abra de novo e rode o mesmo comando de instalacao mais uma vez."
  exit 1
}
Write-Host "Python: $(& $PythonExe --version)"

# 2. baixar ou atualizar
if (Test-Path (Join-Path $Dest ".git")) {
  Write-Host "Atualizando a instalacao em `"$Dest`"..."
  git -C $Dest pull --ff-only
} else {
  Write-Host "Baixando o Ad Animado em `"$Dest`"..."
  git clone $RepoUrl $Dest
}
Set-Location $Dest

# 3. ambiente Python
Write-Host ""
Write-Host "Preparando o ambiente Python (na primeira vez demora alguns minutos)..."
$VenvPython = Join-Path $Dest ".venv\Scripts\python.exe"
if (-not (Test-Path $VenvPython)) { & $PythonExe -m venv .venv }
& $VenvPython -m pip install --upgrade pip -q
& $VenvPython -m pip install -r requirements.txt -q
New-Item -ItemType Directory -Force -Path (Join-Path $env:USERPROFILE "Ads Animados") | Out-Null

# 4. atalho na area de trabalho
try {
  $desktop = [Environment]::GetFolderPath("Desktop")
  $shell = New-Object -ComObject WScript.Shell
  $sc = $shell.CreateShortcut((Join-Path $desktop "Ad Animado.lnk"))
  $sc.TargetPath = "powershell.exe"
  $sc.Arguments = "-NoLogo -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$Dest\iniciar.ps1`""
  $sc.WorkingDirectory = $Dest
  $sc.IconLocation = "$VenvPython,0"
  $sc.Save()
  Write-Host "Criei o atalho `"Ad Animado`" na area de trabalho."
} catch { Write-Host "Nao consegui criar o atalho (sem problema: rode este comando de novo para abrir)." }

Write-Host ""
Write-Host "Pronto. Abrindo http://localhost:$Porta ..."
Write-Host "Primeiro passo: Configuracoes (Claude pela assinatura, Grok, KIE)."
& powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $Dest "iniciar.ps1")
