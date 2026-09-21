#!/usr/bin/env bash
set -Eeuo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")"

PYTHON_BIN="${PYTHON_BIN:-python3}"

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
        "Erreur : Tkinter n'est pas disponible.\n"
        "Sous Debian/Ubuntu : sudo apt install python3-tk\n"
        "Sous Fedora : sudo dnf install python3-tkinter\n"
        f"Détail : {exc}"
    )
try:
    import cv2  # noqa: F401
except Exception:
    raise SystemExit("Erreur : OpenCV manque. Lancez d'abord ./setup.sh")
PY

exec "$PYTHON_BIN" main.py "$@"
