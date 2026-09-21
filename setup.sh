#!/usr/bin/env bash
set -Eeuo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")"
PYTHON_BIN="${PYTHON_BIN:-python3}"
VENV_DIR="${VENV_DIR:-.venv}"

if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  echo "Erreur : $PYTHON_BIN est introuvable. Installe Python 3.11 ou plus récent." >&2
  exit 1
fi

"$PYTHON_BIN" - <<'PY'
import sys
if sys.version_info < (3, 11):
    raise SystemExit("Erreur : Python 3.11+ est requis.")
try:
    import tkinter  # noqa: F401
except Exception as exc:
    raise SystemExit(
        "Tkinter manque sur ce système.\n"
        "Debian/Ubuntu : sudo apt install python3-tk\n"
        "Fedora : sudo dnf install python3-tkinter\n"
        f"Détail : {exc}"
    )
PY

if [ ! -d "$VENV_DIR" ]; then
  echo "Création de l'environnement virtuel : $VENV_DIR"
  "$PYTHON_BIN" -m venv "$VENV_DIR"
fi

VENV_PY="$VENV_DIR/bin/python"
if [ ! -x "$VENV_PY" ]; then
  echo "Erreur : environnement virtuel incomplet : $VENV_DIR" >&2
  exit 1
fi

"$VENV_PY" -m pip install --upgrade pip
"$VENV_PY" -m pip install -r requirements.txt

echo
echo "Installation terminée."
echo "Lancement : ./run_venv.sh"
