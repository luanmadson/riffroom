<#
  Sala de Ensaio: instalador para Windows.
    - cria o ambiente Python do app em %LOCALAPPDATA%\SalaDeEnsaio\venv (fora de pastas sincronizadas);
    - instala as bibliotecas (requirements.txt);
    - gera a música de demonstração em <Músicas>\Sala de Ensaio (se ainda não existir);
    - cria os atalhos "Sala de Ensaio" na Área de Trabalho e no menu Iniciar, e abre o app.
  Uso: dois cliques em instalar.bat, ou
       powershell -ExecutionPolicy Bypass -File instalar.ps1 [-Biblioteca PASTA] [-SemDemo] [-SemAbrir] [-Desinstalar]
#>
param(
  [string[]]$Biblioteca = @(),
  [switch]$SemDemo,
  [switch]$SemAbrir,
  [switch]$Desinstalar
)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$Raiz = Split-Path -Parent $MyInvocation.MyCommand.Path
$Dados = Join-Path $env:LOCALAPPDATA 'SalaDeEnsaio'
$Venv = Join-Path $Dados 'venv'
$PyVenv = Join-Path $Venv 'Scripts\python.exe'
$PywVenv = Join-Path $Venv 'Scripts\pythonw.exe'
$App = Join-Path $Raiz 'app\sala_de_ensaio.py'
$Icone = Join-Path $Raiz 'app\icone.ico'
$Nome = 'Sala de Ensaio'
$Atalhos = @(
  (Join-Path ([Environment]::GetFolderPath('Desktop')) "$Nome.lnk"),
  (Join-Path ([Environment]::GetFolderPath('Programs')) "$Nome.lnk")
)

function Passo([string]$t) { Write-Host ''; Write-Host "== $t" -ForegroundColor Cyan }

if ($Desinstalar) {
  Passo 'Removendo os atalhos e o ambiente Python do app'
  foreach ($a in $Atalhos) { if (Test-Path $a) { Remove-Item $a -Force; Write-Host "  apagado: $a" } }
  if (Test-Path $Venv) { Remove-Item $Venv -Recurse -Force; Write-Host "  apagado: $Venv" }
  Write-Host ''
  Write-Host "Pronto. Suas músicas, tentativas e a configuração ($Dados) ficaram onde estavam."
  exit 0
}

# 1. Python 3.10 ou mais novo (o "py" do python.org; o "python" da Microsoft Store é só um atalho para a loja)
Passo 'Procurando o Python (3.10 ou mais novo)'
function Versao([string]$exe, [string[]]$antes) {
  try {
    $cmd = Get-Command $exe -ErrorAction Stop
    if ($cmd.Source -like '*WindowsApps*') { return $null }
    $v = & $exe @antes -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null
    if ($LASTEXITCODE -eq 0 -and $v) { return [version]($v | Select-Object -First 1).Trim() }
  } catch { }
  return $null
}
$exe = $null; $antes = @()
$v = Versao 'py' @('-3')
if ($v -and $v -ge [version]'3.10') { $exe = 'py'; $antes = @('-3') }
else {
  $v = Versao 'python' @()
  if ($v -and $v -ge [version]'3.10') { $exe = 'python' }
}
if (-not $exe) {
  Write-Host 'Não achei o Python 3.10 ou mais novo.' -ForegroundColor Yellow
  Write-Host 'Instale o Python (grátis) em https://www.python.org/downloads/  ou, no PowerShell:  winget install Python.Python.3.13'
  Write-Host 'Depois rode este instalador de novo.'
  exit 1
}
Write-Host "  Python $v"

# 2. Ambiente do app
Passo "Preparando o ambiente Python do app ($Venv)"
if (-not (Test-Path $PyVenv)) {
  & $exe @antes -m venv $Venv
  if ($LASTEXITCODE) { throw 'Não consegui criar o ambiente Python.' }
} else { Write-Host '  já existe' }

# 3. Bibliotecas
Passo 'Instalando as bibliotecas (numpy, scipy, soundfile, sounddevice, lameenc)'
& $PyVenv -m pip install --upgrade pip --quiet --disable-pip-version-check
& $PyVenv -m pip install -r (Join-Path $Raiz 'requirements.txt') --quiet --disable-pip-version-check
if ($LASTEXITCODE) { throw 'A instalação das bibliotecas falhou (veja a mensagem acima).' }
Write-Host '  pronto'

# 4. Pastas de músicas a mais
foreach ($b in $Biblioteca) {
  Passo "Juntando a pasta de músicas: $b"
  & $PyVenv -I -X utf8 $App --biblioteca $b --configurar
}

# 5. Música de demonstração
if (-not $SemDemo) {
  Passo 'Música de demonstração'
  $musicas = & $PyVenv -I -X utf8 -c "import sys; sys.path.insert(0, sys.argv[1]); import musica; print(musica.pasta_musicas_padrao())" (Join-Path $Raiz 'app')
  $demo = Join-Path $musicas 'Sala de Ensaio\Blues de demonstração'
  if (Test-Path (Join-Path $demo 'ensaio.json')) { Write-Host "  já existe: $demo" }
  else {
    & $PyVenv -I -X utf8 (Join-Path $Raiz 'demo\gerar_demo.py')
    if ($LASTEXITCODE) { throw 'Não consegui gerar a música de demonstração.' }
  }
}

# 6. Atalhos
Passo 'Criando os atalhos "Sala de Ensaio" (Área de Trabalho e menu Iniciar)'
$ws = New-Object -ComObject WScript.Shell
foreach ($a in $Atalhos) {
  $l = $ws.CreateShortcut($a)
  $l.TargetPath = $PywVenv
  $l.Arguments = "-I -X utf8 `"$App`""
  $l.WorkingDirectory = $Raiz
  $l.IconLocation = "$Icone,0"
  $l.Description = 'Tocar e gravar junto com a banda separada'
  $l.Save()
  Write-Host "  $a"
}

if (-not $SemAbrir) {
  Passo 'Abrindo a Sala de Ensaio'
  Start-Process -FilePath $PywVenv -ArgumentList @('-I', '-X', 'utf8', "`"$App`"") -WorkingDirectory $Raiz
}
Write-Host ''
Write-Host 'Pronto! Abra pelo atalho "Sala de Ensaio".' -ForegroundColor Green
