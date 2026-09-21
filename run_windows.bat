@echo off
cd /d "%~dp0"

python -c "import cv2, numpy, psutil" >nul 2>&1
if errorlevel 1 (
  echo Installation des dependances requises ^(OpenCV^)...
  python -m pip install -r requirements.txt
  if errorlevel 1 (
    echo Echec de l'installation des dependances.
    pause
    exit /b 1
  )
)

python main.py
if errorlevel 1 pause
