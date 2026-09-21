@echo off
cd /d "%~dp0"

python -c "import cv2, numpy, psutil" >nul 2>&1
if errorlevel 1 (
  echo Installation des dependances runtime...
  python -m pip install -r requirements.txt
  if errorlevel 1 (
    echo Echec de l'installation des dependances.
    pause
    exit /b 1
  )
)

python -m PyInstaller --version >nul 2>&1
if errorlevel 1 (
  echo PyInstaller n'est pas installe.
  echo Installez-le avec: python -m pip install pyinstaller
  pause
  exit /b 1
)
python -m PyInstaller --noconfirm --clean --onefile --windowed --name ClipboardAgentRelay main.py
if errorlevel 1 (
  echo Echec du build.
  pause
  exit /b 1
)
echo.
echo Build termine: dist\ClipboardAgentRelay.exe
pause
