# Monta a pasta mfref/ (camera virtual Media Foundation) de forma reproduzivel.
# Clona o sample VCamSample da Microsoft, baixa as dependencias NuGet (WIL e
# CppWinRT) e aplica nossos arquivos customizados de mfcam/src/.
# Usado pelo CI (GitHub Actions) e localmente. Idempotente.
$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$mfref = Join-Path $root "mfref"

if (-not (Test-Path $mfref)) {
    Write-Host "Clonando VCamSample..."
    git clone --depth 1 https://github.com/smourier/VCamSample.git $mfref
}

# Pacotes NuGet (zips) -> mfref/packages/
$pkgs = Join-Path $mfref "packages"
New-Item -ItemType Directory -Force -Path $pkgs | Out-Null
$wil = "Microsoft.Windows.ImplementationLibrary.1.0.260126.7"
$cppwinrt = "Microsoft.Windows.CppWinRT.3.0.260520.1"
$nuget = @(
    @{ id = "Microsoft.Windows.ImplementationLibrary"; ver = "1.0.260126.7"; dir = $wil },
    @{ id = "Microsoft.Windows.CppWinRT"; ver = "3.0.260520.1"; dir = $cppwinrt }
)
foreach ($p in $nuget) {
    $dest = Join-Path $pkgs $p.dir
    if (-not (Test-Path (Join-Path $dest "build"))) {
        Write-Host "Baixando NuGet $($p.id) $($p.ver)..."
        $zip = Join-Path $pkgs "$($p.id).zip"
        Invoke-WebRequest -Uri "https://www.nuget.org/api/v2/package/$($p.id)/$($p.ver)" -OutFile $zip
        Expand-Archive -Path $zip -DestinationPath $dest -Force
        Remove-Item $zip -Force
    }
}

# Aplica nossos arquivos customizados por cima do sample.
$src = Join-Path $root "mfcam\src"
Copy-Item (Join-Path $src "CamFXShared.h")    (Join-Path $mfref "VCamSampleSource\") -Force
Copy-Item (Join-Path $src "FrameGenerator.h") (Join-Path $mfref "VCamSampleSource\") -Force
Copy-Item (Join-Path $src "FrameGenerator.cpp") (Join-Path $mfref "VCamSampleSource\") -Force
Copy-Item (Join-Path $src "MediaStream.cpp")  (Join-Path $mfref "VCamSampleSource\") -Force
Copy-Item (Join-Path $src "camfx_vcam.cpp")   (Join-Path $mfref "VCamSample\") -Force
Copy-Item (Join-Path $src "CamFXShared.h")    (Join-Path $mfref "VCamSample\") -Force

Write-Host "mfref pronto."
