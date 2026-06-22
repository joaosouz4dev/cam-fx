@echo off
REM Compila o DirectShow BaseClasses como biblioteca estatica (strmbasex64.lib).
setlocal
call "C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\VC\Auxiliary\Build\vcvars64.bat" >nul 2>&1
cd /d "%~dp0baseclasses"

set DEFS=/D_WIN32_WINNT=0x0A00 /DWINVER=0x0A00 /DUNICODE /D_UNICODE /DWIN32 /D_WINDOWS
set FLAGS=/nologo /c /EHsc /MT /O2 /W0 /I.

echo Compilando baseclasses...
cl %FLAGS% %DEFS% *.cpp >..\baseclasses_build.out 2>&1
if errorlevel 1 (
  echo FALHA na compilacao. Ultimas linhas:
  type ..\baseclasses_build.out
  exit /b 1
)

lib /nologo /OUT:..\strmbasex64.lib *.obj >>..\baseclasses_build.out 2>&1
if errorlevel 1 ( echo FALHA no lib & type ..\baseclasses_build.out & exit /b 1 )
echo OK: strmbasex64.lib gerada
