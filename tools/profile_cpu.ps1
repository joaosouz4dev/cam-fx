# Le e resume as linhas "PROFILE:" do camfx.log (geradas com CAMFX_PROFILE=1).
#
# Uso:
#   1) Rodar o app com o profiling ligado, com o face swap como voce usa:
#        $env:CAMFX_PROFILE = "1"
#        .venv\Scripts\python.exe main.py
#      (ative o preview + face swap, deixe ~10s, feche o app)
#   2) Rodar este resumo:
#        .venv\Scripts\python.exe -m nada  # (nao precisa de python)
#        pwsh -File tools\profile_cpu.ps1
#      ou no PowerShell:  ./tools/profile_cpu.ps1
#
# Mostra as ultimas linhas cruas e a MEDIA de cada etapa, apontando o maior
# consumidor - para decidir onde cortar CPU com dados.

param(
    [int]$Ultimas = 15,          # quantas linhas recentes considerar na media
    [string]$LogPath = "$env:LOCALAPPDATA\CamFX\camfx.log"
)

if (-not (Test-Path $LogPath)) {
    Write-Host "Log nao encontrado: $LogPath" -ForegroundColor Red
    Write-Host "Rode o app com `$env:CAMFX_PROFILE='1' primeiro."
    exit 1
}

$linhas = Get-Content $LogPath | Where-Object { $_ -match 'PROFILE:' }
if (-not $linhas) {
    Write-Host "Nenhuma linha PROFILE: no log." -ForegroundColor Yellow
    Write-Host "Confirme que rodou com `$env:CAMFX_PROFILE='1' e o preview ativo."
    exit 1
}

$amostra = $linhas | Select-Object -Last $Ultimas
Write-Host "=== ultimas $($amostra.Count) amostras (1 por segundo) ===" -ForegroundColor Cyan
$amostra | ForEach-Object { Write-Host "  $_" }

# Extrai os numeros de cada campo (swap/framing/blur/send/ocioso/trabalho/FPS).
$campos = 'swap', 'framing', 'blur', 'send', 'ocioso', 'trabalho'
$soma = @{}; $campos | ForEach-Object { $soma[$_] = 0.0 }
$somaFps = 0.0; $n = 0

foreach ($l in $amostra) {
    $ok = $true
    foreach ($c in $campos) {
        if ($l -match "$c=(\d+(?:\.\d+)?)%") { $soma[$c] += [double]$Matches[1] }
        else { $ok = $false }
    }
    if ($l -match '(\d+(?:\.\d+)?)\s*FPS') { $somaFps += [double]$Matches[1] }
    if ($ok) { $n++ }
}

if ($n -eq 0) { Write-Host "Nao consegui parsear as amostras." -ForegroundColor Red; exit 1 }

Write-Host ""
Write-Host "=== media das $n amostras ===" -ForegroundColor Cyan
Write-Host ("  FPS de saida:  {0:N0}" -f ($somaFps / $n))
$medias = @{}
foreach ($c in $campos) { $medias[$c] = $soma[$c] / $n }
foreach ($c in 'swap', 'framing', 'blur', 'send', 'ocioso', 'trabalho') {
    Write-Host ("  {0,-9}: {1,5:N0}%" -f $c, $medias[$c])
}

# Aponta o maior consumidor entre as etapas de trabalho.
$etapas = 'swap', 'framing', 'blur', 'send'
$maior = $etapas | Sort-Object { $medias[$_] } -Descending | Select-Object -First 1
Write-Host ""
Write-Host ("=> maior consumidor: {0} ({1:N0}% do tempo)" -f $maior, $medias[$maior]) -ForegroundColor Green
if (($somaFps / $n) -gt 33) {
    Write-Host ("=> FPS de saida ({0:N0}) acima de 30: limitar o FPS ja corta trabalho." -f ($somaFps / $n)) -ForegroundColor Green
}
