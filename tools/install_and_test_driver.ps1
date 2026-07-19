# Instala o driver CamFX RECEM-COMPILADO e testa a leitura da resolucao dinamica.
#
# Faz, em um comando (PRECISA DE ADMIN):
#   1. registra a DLL nova (regsvr32) - a camera "CamFX" fica disponivel no SO;
#   2. sobe o camfx_vcam.exe (host que chama MFCreateVirtualCamera);
#   3. escreve um frame de teste 1080p pelo Python (virtualcam);
#   4. le C:\ProgramData\CamFX\dll.log e mostra se o driver leu a resolucao certa.
#
# Uso (PowerShell COMO ADMINISTRADOR):
#   pwsh -File tools\install_and_test_driver.ps1
#
# Para DESINSTALAR o driver de teste depois:
#   pwsh -File tools\install_and_test_driver.ps1 -Uninstall
#
# ATENCAO: registra uma camera virtual no seu sistema. Reversivel com -Uninstall.

param(
    [switch]$Uninstall
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$dll = Join-Path $root "mfref\VCamSampleSource\x64\Release\VCamSampleSource.dll"
$hostExe = Join-Path $root "mfref\VCamSample\camfx_vcam.exe"
$log = "C:\ProgramData\CamFX\dll.log"

function Assert-Admin {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    $p = New-Object Security.Principal.WindowsPrincipal($id)
    if (-not $p.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        Write-Host "=============================================================" -ForegroundColor Red
        Write-Host " PRECISA DE ADMINISTRADOR para registrar a camera no sistema." -ForegroundColor Red
        Write-Host " Feche este terminal e abra o PowerShell COMO ADMINISTRADOR:" -ForegroundColor Yellow
        Write-Host "   menu Iniciar -> digite 'PowerShell' -> botao direito ->" -ForegroundColor Yellow
        Write-Host "   'Executar como administrador'. Depois rode de novo:" -ForegroundColor Yellow
        Write-Host "   cd $root" -ForegroundColor Yellow
        Write-Host "   pwsh -File tools\install_and_test_driver.ps1" -ForegroundColor Yellow
        Write-Host "=============================================================" -ForegroundColor Red
        exit 1
    }
}

# regsvr32 e um app GUI: nao seta $LASTEXITCODE nem bloqueia sem -Wait, e mostra
# erros num popup. Rodamos com Start-Process -Wait -PassThru para pegar o
# ExitCode de verdade. Passamos os argumentos como ARRAY (nao string) e evitamos
# o nome $Args, que e uma variavel AUTOMATICA do PowerShell (por isso o regsvr32
# recebia args vazios -> "forneca um nome binario").
function Invoke-Regsvr32 {
    param([string[]]$RegArgs)
    $p = Start-Process -FilePath "regsvr32.exe" -ArgumentList $RegArgs -Wait -PassThru -WindowStyle Hidden
    return $p.ExitCode
}

if ($Uninstall) {
    Assert-Admin
    Write-Host "=== desregistrando o driver CamFX de teste ===" -ForegroundColor Cyan
    Get-Process camfx_vcam -ErrorAction SilentlyContinue | ForEach-Object { try { $_.Kill() } catch {} }
    $code = Invoke-Regsvr32 @("/u", "/s", $dll)
    Write-Host "Driver desregistrado (exit=$code). A camera 'CamFX' some do sistema."
    exit 0
}

Assert-Admin

if (-not (Test-Path $dll)) {
    Write-Host "DLL nao encontrada: $dll" -ForegroundColor Red
    Write-Host "Rode ./setup_driver.ps1 e ./build_driver.ps1 primeiro."
    exit 1
}

# Zera o log para so vermos as linhas DESTE teste.
New-Item -ItemType Directory -Force -Path "C:\ProgramData\CamFX" | Out-Null
Set-Content -Path $log -Value "=== teste $(Get-Date -Format 'HH:mm:ss') ===" -ErrorAction SilentlyContinue

Write-Host "=== 1) registrando a DLL nova (regsvr32) ===" -ForegroundColor Cyan
$code = Invoke-Regsvr32 @("/s", $dll)
if ($code -ne 0) {
    Write-Host "regsvr32 FALHOU (exit=$code)." -ForegroundColor Red
    Write-Host "Causas comuns:" -ForegroundColor Yellow
    Write-Host "  - exit 5 = acesso negado: NAO esta como admin (abra o PowerShell como administrador)."
    Write-Host "  - exit 3/0x80070005 = DLL ja registrada por outra versao: desregistre antes com -Uninstall."
    Write-Host "  - erro de DllRegisterServer: a DLL nao exporta o registro (build incompleto)."
    exit 1
}
Write-Host "  DLL registrada (exit=0)."

Write-Host "=== 2) escrevendo um frame de teste 1080p pelo Python ===" -ForegroundColor Cyan
$py = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) { $py = "python" }
# O writer roda de um arquivo temporario; injetamos a RAIZ do projeto no
# sys.path para o `import camfx` funcionar (senao: ModuleNotFoundError).
$rootPy = $root.Replace('\', '\\')
$writer = @"
import sys, time
sys.path.insert(0, r"$root")
import numpy as np
import camfx.virtualcam as vc
cam = vc.CamFXVirtualCamera(width=1920, height=1080, fps=30)
frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
frame[:, :, 1] = 180  # verde, so para ter conteudo
print("escrevendo frames 1080p por 6s no frame.bin...")
t = time.time()
while time.time() - t < 6:
    cam.send(frame); time.sleep(0.03)
cam.close()
print("pronto: 1080p escrito no frame.bin.")
"@
$tmp = Join-Path $env:TEMP "camfx_writer.py"
Set-Content -Path $tmp -Value $writer

Write-Host "=== 3) subindo o camfx_vcam.exe (host da camera) ===" -ForegroundColor Cyan
$hostProc = Start-Process -FilePath $hostExe -PassThru -WindowStyle Hidden
Start-Sleep -Seconds 1

# roda o writer (da raiz do projeto, para o camfx no path funcionar tambem)
Push-Location $root
& $py $tmp
Pop-Location

Start-Sleep -Seconds 1
try { $hostProc.Kill() } catch {}
Get-Process camfx_vcam -ErrorAction SilentlyContinue | ForEach-Object { try { $_.Kill() } catch {} }

Write-Host ""
Write-Host "=== 4) o que o driver (dll.log) leu ===" -ForegroundColor Cyan
if (Test-Path $log) {
    $lines = Get-Content $log | Select-Object -Last 20
    $lines | ForEach-Object { Write-Host "  $_" }
    Write-Host ""
    # Procura sinais de sucesso/erro
    $fill = $lines | Where-Object { $_ -match 'FillBitmap' } | Select-Object -Last 3
    if ($fill) {
        Write-Host "=> o driver chamou FillBitmap (leu o frame.bin). Confira w=1920 h=1080:" -ForegroundColor Green
        $fill | ForEach-Object { Write-Host "     $_" }
    } else {
        Write-Host "=> Nenhuma linha FillBitmap no log. O host pode nao ter conseguido criar a camera" -ForegroundColor Yellow
        Write-Host "   (MFCreateVirtualCamera exige a DLL registrada; confira acima se o registro deu certo)."
    }
} else {
    Write-Host "  dll.log nao encontrado - o driver nao chegou a rodar." -ForegroundColor Yellow
}

Write-Host ""
Write-Host "Para o teste REAL (imagem): abra o CamFX + o app Camera do Windows e selecione 'CamFX'."
Write-Host "Para remover a camera de teste: pwsh -File tools\install_and_test_driver.ps1 -Uninstall"
