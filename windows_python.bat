@echo off
setlocal EnableExtensions
set "CAR_PYTHON_EXE="

rem Prefer the Windows Python Launcher when available. This avoids an unrelated
rem MSYS2/Git/Cygwin python.exe taking precedence through PATH.
for /f "delims=" %%P in ('py -3 -c "import sys; print(sys.executable)" 2^>nul') do (
  if not defined CAR_PYTHON_EXE set "CAR_PYTHON_EXE=%%P"
)

if not defined CAR_PYTHON_EXE (
  for /f "delims=" %%P in ('python -c "import sys; print(sys.executable)" 2^>nul') do (
    if not defined CAR_PYTHON_EXE set "CAR_PYTHON_EXE=%%P"
  )
)

if not defined CAR_PYTHON_EXE (
  echo ERREUR : aucun Python Windows utilisable n'a ete trouve.
  echo Installe Python 3.11 ou plus recent depuis python.org, puis relance ce script.
  exit /b 1
)

"%CAR_PYTHON_EXE%" -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>&1
if errorlevel 1 (
  echo ERREUR : Python 3.11 ou plus recent est requis.
  echo Interpreteur detecte : "%CAR_PYTHON_EXE%"
  exit /b 1
)

if /i "%~1"=="--ensure-pip" (
  "%CAR_PYTHON_EXE%" -m pip --version >nul 2>&1
  if errorlevel 1 (
    echo pip est absent pour "%CAR_PYTHON_EXE%". Tentative via ensurepip...
    "%CAR_PYTHON_EXE%" -m ensurepip --upgrade >nul 2>&1
  )

  "%CAR_PYTHON_EXE%" -m pip --version >nul 2>&1
  if errorlevel 1 (
    echo.
    echo ERREUR : pip n'est pas disponible pour :
    echo   "%CAR_PYTHON_EXE%"
    echo.
    echo Si le chemin contient \msys64\ucrt64\, deux solutions :
    echo   1. Recommande : installer Python Windows 3.11+ depuis python.org
    echo      avec le Python Launcher ^(py.exe^).
    echo   2. MSYS2 : installer le paquet mingw-w64-ucrt-x86_64-python-pip
    echo      depuis un terminal MSYS2 UCRT64.
    exit /b 1
  )
)

endlocal & set "CAR_PYTHON_EXE=%CAR_PYTHON_EXE%"
exit /b 0
