@echo off
REM Compila o DLL do source filter CamFX (CamFXSource.dll).
REM Requer strmbasex64.lib (rode build_baseclasses.bat antes).
setlocal
call "C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\VC\Auxiliary\Build\vcvars64.bat" >nul 2>&1
cd /d "%~dp0"

if not exist strmbasex64.lib (
  echo strmbasex64.lib nao encontrada. Rode build_baseclasses.bat primeiro.
  exit /b 1
)

set DEFS=/D_WIN32_WINNT=0x0A00 /DWINVER=0x0A00 /DUNICODE /D_UNICODE /DWIN32 /D_WINDOWS
set FLAGS=/nologo /c /EHsc /MT /O2 /W3 /Ibaseclasses

echo Compilando camfx_source.cpp...
cl %FLAGS% %DEFS% camfx_source.cpp >driver_build.out 2>&1
if errorlevel 1 ( echo FALHA na compilacao. & type driver_build.out & exit /b 1 )

echo Linkando CamFXSource.dll...
link /nologo /DLL /OUT:CamFXSource.dll /DEF:camfx.def camfx_source.obj ^
  strmbasex64.lib ^
  strmiids.lib winmm.lib ole32.lib oleaut32.lib user32.lib gdi32.lib advapi32.lib uuid.lib ^
  >>driver_build.out 2>&1
if errorlevel 1 ( echo FALHA no link. & type driver_build.out & exit /b 1 )

echo OK: CamFXSource.dll gerada
