@echo off
setlocal EnableExtensions
cd /d "%~dp0"

call "%~dp0windows_python.bat"
if errorlevel 1 (
  pause
  exit /b 1
)

echo Python selectionne : "%CAR_PYTHON_EXE%"

"%CAR_PYTHON_EXE%" -c "import tkinter" >nul 2>&1
if errorlevel 1 (
  echo.
  echo ERREUR : Tkinter n'est pas disponible dans cet interpreteur.
  echo Installe Python Windows 3.11+ depuis python.org avec Tcl/Tk, puis relance.
  echo Interpreteur detecte : "%CAR_PYTHON_EXE%"
  pause
  exit /b 1
)

"%CAR_PYTHON_EXE%" -c "import cv2, numpy, psutil" >nul 2>&1
if errorlevel 1 (
  echo Installation des dependances runtime...
  call "%~dp0windows_python.bat" --ensure-pip
  if errorlevel 1 (
    pause
    exit /b 1
  )

  "%CAR_PYTHON_EXE%" -m pip install -r requirements.txt
  if errorlevel 1 (
    echo Echec de l'installation des dependances.
    pause
    exit /b 1
  )
)

"%CAR_PYTHON_EXE%" main.py
if errorlevel 1 pause
endlocal
