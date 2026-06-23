# Compila a camera virtual Media Foundation: o DLL (VCamSampleSource.dll, via
# MSBuild) e o helper headless (camfx_vcam.exe, via cl). Localiza o MSVC com
# vswhere, entao funciona no CI (windows-latest) e localmente.
# Pre-requisito: setup_driver.ps1 ja rodou (mfref/ montado).
$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$mfref = Join-Path $root "mfref"

# Localiza o Visual Studio / Build Tools (MSBuild + vcvars).
# 1) tenta o vswhere (funciona no CI e em instalacoes registradas);
# 2) fallback p/ caminhos conhecidos (BuildTools standalone que o vswhere
#    nao cataloga).
$vsPath = $null
$vswhere = "${env:ProgramFiles(x86)}\Microsoft Visual Studio\Installer\vswhere.exe"
if (Test-Path $vswhere) {
    $vsPath = & $vswhere -latest -products * `
        -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 `
        -property installationPath
}
if (-not $vsPath) {
    foreach ($cand in @(
        "${env:ProgramFiles}\Microsoft Visual Studio\2022\Enterprise",
        "${env:ProgramFiles}\Microsoft Visual Studio\2022\Professional",
        "${env:ProgramFiles}\Microsoft Visual Studio\2022\Community",
        "${env:ProgramFiles}\Microsoft Visual Studio\2022\BuildTools",
        "${env:ProgramFiles(x86)}\Microsoft Visual Studio\2022\BuildTools")) {
        if (Test-Path (Join-Path $cand "VC\Tools\MSVC")) { $vsPath = $cand; break }
    }
}
if (-not $vsPath) { throw "Visual Studio / Build Tools com C++ nao encontrado." }
$msbuild = Join-Path $vsPath "MSBuild\Current\Bin\MSBuild.exe"
$vcvars = Join-Path $vsPath "VC\Auxiliary\Build\vcvars64.bat"
Write-Host "VS: $vsPath"

# SDK do Windows: usa o mais novo instalado.
$sdkRoot = "${env:ProgramFiles(x86)}\Windows Kits\10\Include"
$sdkVer = (Get-ChildItem $sdkRoot -Directory | Sort-Object Name -Descending | Select-Object -First 1).Name
Write-Host "Windows SDK: $sdkVer"

# 1) DLL via MSBuild.
$proj = Join-Path $mfref "VCamSampleSource\VCamSampleSource.vcxproj"
& $msbuild $proj /p:Configuration=Release /p:Platform=x64 /p:PlatformToolset=v143 `
    /p:WindowsTargetPlatformVersion=$sdkVer /v:minimal
if ($LASTEXITCODE -ne 0) { throw "Falha ao compilar o DLL." }

# 2) Helper headless via cl (dentro do ambiente do vcvars).
$helperDir = Join-Path $mfref "VCamSample"
$bat = @"
call "$vcvars"
cd /d "$helperDir"
cl /nologo /EHsc /std:c++17 /DUNICODE /D_UNICODE camfx_vcam.cpp /Fe:camfx_vcam.exe /link mfsensorgroup.lib mfplat.lib ole32.lib
"@
$tmp = Join-Path $env:TEMP "camfx_build_helper.bat"
Set-Content -Path $tmp -Value $bat -Encoding ascii
cmd /c $tmp
if (-not (Test-Path (Join-Path $helperDir "camfx_vcam.exe"))) { throw "Falha ao compilar o helper." }

Write-Host "Driver compilado:"
Write-Host "  $(Join-Path $mfref 'VCamSampleSource\x64\Release\VCamSampleSource.dll')"
Write-Host "  $(Join-Path $helperDir 'camfx_vcam.exe')"
