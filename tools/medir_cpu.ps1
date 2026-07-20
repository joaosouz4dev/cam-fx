# Coleta de CPU do CamFX em UM comando.
#
# Faz o ciclo completo: fecha qualquer CamFX aberto (o instalado nao tem a
# instrumentacao), sobe o app DA FONTE com CAMFX_PROFILE=1, espera voce testar,
# e ao fechar imprime o resumo por etapa automaticamente.
#
# COMO USAR:
#   pwsh -File tools\medir_cpu.ps1
#   (ou no PowerShell:  ./tools/medir_cpu.ps1)
#
# QUANDO O APP ABRIR:
#   1. Ative o preview (e o face swap, se quiser medir esse cenario).
#   2. Deixe rodando ~10-15 segundos.
#   3. FECHE o app (X ou bandeja -> sair).
#   O resumo aparece sozinho no terminal assim que o app fechar.

$ErrorActionPreference = "Stop"
$raiz = Split-Path -Parent $PSScriptRoot
Set-Location $raiz

$py = Join-Path $raiz ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) {
    Write-Host "python do venv nao encontrado em $py" -ForegroundColor Red
    exit 1
}

Write-Host "=== fechando CamFX aberto (o instalado nao tem profiling) ===" -ForegroundColor Cyan
foreach ($nome in "CamFX", "camfx_vcam") {
    Get-Process -Name $nome -ErrorAction SilentlyContinue | ForEach-Object {
        try { $_.Kill() } catch {}
    }
}
Start-Sleep -Seconds 2

$log = Join-Path $env:LOCALAPPDATA "CamFX\camfx.log"
$marcador = "=== MEDIR_CPU sessao $(Get-Random) ==="
# Marca o log para o resumo considerar so as linhas DESTA sessao.
if (Test-Path $log) { Add-Content -Path $log -Value $marcador }

Write-Host ""
Write-Host "=== subindo o CamFX DA FONTE com CAMFX_PROFILE=1 ===" -ForegroundColor Cyan
Write-Host "  >> Ative o preview, deixe ~10-15s, e FECHE o app." -ForegroundColor Yellow
Write-Host "  >> O resumo aparece aqui quando voce fechar." -ForegroundColor Yellow
Write-Host ""

$env:CAMFX_PROFILE = "1"
& $py (Join-Path $raiz "main.py")

Write-Host ""
Write-Host "=== app fechado; resumindo as amostras desta sessao ===" -ForegroundColor Cyan

# Le so as linhas PROFILE apos o marcador desta sessao.
$linhas = Get-Content $log
$idx = ($linhas | Select-String -SimpleMatch $marcador | Select-Object -Last 1).LineNumber
if ($idx) { $linhas = $linhas[$idx..($linhas.Count - 1)] }
$prof = $linhas | Where-Object { $_ -match 'PROFILE:' }

if (-not $prof) {
    Write-Host "Nenhuma linha PROFILE: nesta sessao." -ForegroundColor Yellow
    Write-Host "Voce ativou o preview? O pipeline so gera PROFILE quando esta transmitindo."
    exit 0
}

# Reusa o resumo do profile_cpu.ps1 gravando as linhas num log temporario.
$tmp = Join-Path $env:TEMP "camfx_profile_sessao.log"
$prof | Set-Content -Path $tmp
& (Join-Path $PSScriptRoot "profile_cpu.ps1") -LogPath $tmp -Ultimas 20
