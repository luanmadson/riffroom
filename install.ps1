<#
  SPDX-License-Identifier: GPL-3.0-or-later
  Riffroom installer for Windows.
    - creates the app's Python environment in %LOCALAPPDATA%\Riffroom\venv (outside synced folders);
    - installs the libraries (requirements.txt) and, with -AI, the local AI (torch, Demucs, beat-this, librosa);
    - makes the demo song in <Music>\Riffroom\Demo (if it is not there yet);
    - creates the "Riffroom" shortcuts on the Desktop and in the Start menu;
    - registers Riffroom for AI assistants (Claude Code and Claude Desktop, when installed);
    - opens the app.
  Usage: double-click install.bat, or
         powershell -ExecutionPolicy Bypass -File install.ps1 [-AI] [-GPU] [-Language en|pt|es] [-Library FOLDER]
                    [-NoDemo] [-NoOpen] [-NoClaude] [-Uninstall]
#>
param(
  [switch]$AI,
  [switch]$GPU,
  [ValidateSet('', 'en', 'pt', 'es')][string]$Language = '',
  [string[]]$Library = @(),
  [switch]$NoDemo,
  [switch]$NoOpen,
  [switch]$NoClaude,
  [switch]$Uninstall
)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Data = Join-Path $env:LOCALAPPDATA 'Riffroom'
$Venv = Join-Path $Data 'venv'
$Py = Join-Path $Venv 'Scripts\python.exe'
$Pyw = Join-Path $Venv 'Scripts\pythonw.exe'
$App = Join-Path $Root 'app\riffroom.py'
$Icon = Join-Path $Root 'app\web\icon.ico'
$Shortcuts = @(
  (Join-Path ([Environment]::GetFolderPath('Desktop')) 'Riffroom.lnk'),
  (Join-Path ([Environment]::GetFolderPath('Programs')) 'Riffroom.lnk')
)

$ui = (Get-UICulture).TwoLetterISOLanguageName
$Lang = if ($ui -eq 'pt') { 'pt' } elseif ($ui -eq 'es') { 'es' } else { 'en' }
$M = @{
  en = @{ uninst = 'Removing the shortcuts, the AI registration and the app''s Python environment'; kept = 'Done. Your songs, takes and settings ({0}) stay where they are.'
          py = 'Looking for Python (3.10 or newer)'; nopy = 'Python 3.10 or newer was not found.'; getpy = 'Install Python (free) from https://www.python.org/downloads/  or, in PowerShell:  winget install Python.Python.3.13'
          again = 'Then run this installer again.'; venv = 'Preparing the app''s Python environment ({0})'; exists = 'already there'
          libs = 'Installing the libraries (numpy, scipy, soundfile, sounddevice, lameenc, mcp)'; ready = 'ready'
          ai = 'Installing the local AI ({0}: torch, Demucs, beat-this, librosa) - a big download, a few minutes'
          aifail = 'The AI installation failed (see above). Riffroom works without it; try install.bat -AI again later.'
          libfail = 'The installation of the libraries failed (see the message above).'
          lib = 'Adding the music folder: {0}'; demo = 'Demo song'; demofail = 'Could not make the demo song.'
          sc = 'Creating the "Riffroom" shortcuts (Desktop and Start menu)'; claude = 'Registering Riffroom for AI assistants (MCP)'
          open = 'Opening Riffroom'; done = 'Done! Open it from the "Riffroom" shortcut.'; desc = 'Play and record along with the separated band' }
  pt = @{ uninst = 'Removendo os atalhos, o registro para IA e o ambiente Python do app'; kept = 'Pronto. Suas músicas, tentativas e configurações ({0}) ficaram onde estavam.'
          py = 'Procurando o Python (3.10 ou mais novo)'; nopy = 'Não achei o Python 3.10 ou mais novo.'; getpy = 'Instale o Python (grátis) em https://www.python.org/downloads/  ou, no PowerShell:  winget install Python.Python.3.13'
          again = 'Depois rode este instalador de novo.'; venv = 'Preparando o ambiente Python do app ({0})'; exists = 'já existe'
          libs = 'Instalando as bibliotecas (numpy, scipy, soundfile, sounddevice, lameenc, mcp)'; ready = 'pronto'
          ai = 'Instalando a IA local ({0}: torch, Demucs, beat-this, librosa) - download grande, alguns minutos'
          aifail = 'A instalação da IA falhou (veja acima). O Riffroom funciona sem ela; tente install.bat -AI de novo depois.'
          libfail = 'A instalação das bibliotecas falhou (veja a mensagem acima).'
          lib = 'Juntando a pasta de músicas: {0}'; demo = 'Música de demonstração'; demofail = 'Não consegui gerar a música de demonstração.'
          sc = 'Criando os atalhos "Riffroom" (Área de Trabalho e menu Iniciar)'; claude = 'Registrando o Riffroom para assistentes de IA (MCP)'
          open = 'Abrindo o Riffroom'; done = 'Pronto! Abra pelo atalho "Riffroom".'; desc = 'Tocar e gravar junto com a banda separada' }
  es = @{ uninst = 'Quitando los accesos directos, el registro para IA y el entorno Python de la app'; kept = 'Listo. Tus canciones, tomas y ajustes ({0}) quedan donde estaban.'
          py = 'Buscando Python (3.10 o más nuevo)'; nopy = 'No se encontró Python 3.10 o más nuevo.'; getpy = 'Instala Python (gratis) desde https://www.python.org/downloads/  o, en PowerShell:  winget install Python.Python.3.13'
          again = 'Después ejecuta este instalador de nuevo.'; venv = 'Preparando el entorno Python de la app ({0})'; exists = 'ya existe'
          libs = 'Instalando las bibliotecas (numpy, scipy, soundfile, sounddevice, lameenc, mcp)'; ready = 'listo'
          ai = 'Instalando la IA local ({0}: torch, Demucs, beat-this, librosa) - descarga grande, unos minutos'
          aifail = 'La instalación de la IA falló (ver arriba). Riffroom funciona sin ella; prueba install.bat -AI de nuevo más tarde.'
          libfail = 'La instalación de las bibliotecas falló (ver el mensaje de arriba).'
          lib = 'Agregando la carpeta de canciones: {0}'; demo = 'Canción de demostración'; demofail = 'No se pudo crear la canción de demostración.'
          sc = 'Creando los accesos directos "Riffroom" (Escritorio y menú Inicio)'; claude = 'Registrando Riffroom para asistentes de IA (MCP)'
          open = 'Abriendo Riffroom'; done = '¡Listo! Ábrelo desde el acceso directo "Riffroom".'; desc = 'Tocar y grabar junto con la banda separada' }
}
function T([string]$k) { return $M[$Lang][$k] }   # PowerShell names ignore case: never call another variable $l
function Step([string]$t) { Write-Host ''; Write-Host "== $t" -ForegroundColor Cyan }

if ($Uninstall) {
  Step (T 'uninst')
  foreach ($s in $Shortcuts) { if (Test-Path $s) { Remove-Item $s -Force; Write-Host "  - $s" } }
  if (Test-Path $Py) { & $Py -I -X utf8 (Join-Path $Root 'app\setup_claude.py') unregister }
  if (Test-Path $Venv) { Remove-Item $Venv -Recurse -Force; Write-Host "  - $Venv" }
  Write-Host ''
  Write-Host ((T 'kept') -f $Data)
  exit 0
}

# 1. Python 3.10 or newer (the "py" launcher of python.org; the "python" of the Microsoft Store is only a link to the store)
Step (T 'py')
function Get-PyVersion([string]$exe, [string[]]$pre) {
  try {
    $cmd = Get-Command $exe -ErrorAction Stop
    if ($cmd.Source -like '*WindowsApps*') { return $null }
    $v = & $exe @pre -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null
    if ($LASTEXITCODE -eq 0 -and $v) { return [version]($v | Select-Object -First 1).Trim() }
  } catch { }
  return $null
}
$exe = $null; $pre = @()
$v = Get-PyVersion 'py' @('-3')
if ($v -and $v -ge [version]'3.10') { $exe = 'py'; $pre = @('-3') }
else { $v = Get-PyVersion 'python' @(); if ($v -and $v -ge [version]'3.10') { $exe = 'python' } }
if (-not $exe) {
  Write-Host (T 'nopy') -ForegroundColor Yellow
  Write-Host (T 'getpy')
  Write-Host (T 'again')
  exit 1
}
Write-Host "  Python $v"

# 2. Environment of the app
Step ((T 'venv') -f $Venv)
if (-not (Test-Path $Py)) {
  & $exe @pre -m venv $Venv
  if ($LASTEXITCODE) { throw 'venv' }
} else { Write-Host "  $(T 'exists')" }

# 3. Libraries
Step (T 'libs')
& $Py -m pip install --upgrade pip --quiet --disable-pip-version-check
& $Py -m pip install -r (Join-Path $Root 'requirements.txt') --quiet --disable-pip-version-check
if ($LASTEXITCODE) { throw (T 'libfail') }
Write-Host "  $(T 'ready')"

# 4. Local AI (optional). torch 2.11, because torchaudio (needed by beat-this) stops at 2.11
if ($AI) {
  $kind = if ($GPU) { 'CUDA' } else { 'CPU' }
  Step ((T 'ai') -f $kind)
  $index = if ($GPU) { 'https://download.pytorch.org/whl/cu128' } else { 'https://download.pytorch.org/whl/cpu' }
  $cons = Join-Path $Data 'constraints-ai.txt'
  Set-Content -Path $cons -Value "torch==2.11.*`r`ntorchaudio==2.11.*" -Encoding ascii
  & $Py -m pip install "torch==2.11.*" "torchaudio==2.11.*" --index-url $index --quiet --disable-pip-version-check
  if (-not $LASTEXITCODE) { & $Py -m pip install -r (Join-Path $Root 'requirements-ai.txt') -c $cons --quiet --disable-pip-version-check }
  if ($LASTEXITCODE) { Write-Host (T 'aifail') -ForegroundColor Yellow } else { Write-Host "  $(T 'ready')" }
}

# 5. Language and extra music folders
$cfgArgs = @()
foreach ($b in $Library) { Step ((T 'lib') -f $b); $cfgArgs += @('--library', $b) }
if ($Language) { $cfgArgs += @('--language', $Language) }
if ($cfgArgs.Count) { & $Py -I -X utf8 $App @cfgArgs --configure }

# 6. Demo song
if (-not $NoDemo) {
  Step (T 'demo')
  $music = & $Py -I -X utf8 -c "import sys; sys.path.insert(0, sys.argv[1]); import song; print(song.default_library())" (Join-Path $Root 'app')
  $demo = Join-Path $music 'Demo'
  if (Test-Path (Join-Path $demo 'riffroom.json')) { Write-Host "  $(T 'exists'): $demo" }
  else {
    & $Py -I -X utf8 (Join-Path $Root 'demo\make_demo.py')
    if ($LASTEXITCODE) { throw (T 'demofail') }
  }
}

# 7. Shortcuts
Step (T 'sc')
$ws = New-Object -ComObject WScript.Shell
foreach ($s in $Shortcuts) {
  $lnk = $ws.CreateShortcut($s)
  $lnk.TargetPath = $Pyw
  $lnk.Arguments = "-I -X utf8 `"$App`""
  $lnk.WorkingDirectory = $Root
  $lnk.IconLocation = "$Icon,0"
  $lnk.Description = (T 'desc')
  $lnk.Save()
  Write-Host "  $s"
}

# 8. AI assistants (MCP)
if (-not $NoClaude) {
  Step (T 'claude')
  & $Py -I -X utf8 (Join-Path $Root 'app\setup_claude.py') register
}

if (-not $NoOpen) {
  Step (T 'open')
  Start-Process -FilePath $Pyw -ArgumentList @('-I', '-X', 'utf8', "`"$App`"") -WorkingDirectory $Root
}
Write-Host ''
Write-Host (T 'done') -ForegroundColor Green
