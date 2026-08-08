@echo off
setlocal
cd /d "%~dp0"
where py >nul 2>nul
if %errorlevel%==0 (
  set "AEROPT_PY=py -3"
) else (
  set "AEROPT_PY=python"
)
%AEROPT_PY% -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" || goto :python_error
if not exist ".venv\Scripts\python.exe" (
  echo [AeroOpt] Python ortami hazirlaniyor...
  %AEROPT_PY% -m venv .venv || goto :error
  ".venv\Scripts\python.exe" -m pip install --upgrade pip || goto :error
  ".venv\Scripts\python.exe" -m pip install -r requirements.txt || goto :error
)
echo [AeroOpt] Arayuz baslatiliyor...
".venv\Scripts\python.exe" server.py
goto :eof
:error
echo.
echo Kurulum tamamlanamadi. Python 3.10+ kurulu oldugunu kontrol edin.
pause
exit /b 1
:python_error
echo.
echo AeroOpt icin 64-bit Python 3.10 veya daha yenisi gerekli.
echo Indirme: https://www.python.org/downloads/windows/
echo Kurulumda "Add python.exe to PATH" secenegini isaretleyin.
pause
exit /b 1
