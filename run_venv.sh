#!/usr/bin/env bash
set -Eeuo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")"
VENV_DIR="${VENV_DIR:-.venv}"
VENV_PY="$VENV_DIR/bin/python"

if [ ! -x "$VENV_PY" ]; then
  echo "L'environnement virtuel n'existe pas encore." >&2
  echo "Lance d'abord : ./setup.sh" >&2
  exit 1
fi

"$VENV_PY" - <<'PY'
try:
    import tkinter  # noqa: F401
except Exception as exc:
    raise SystemExit(f"Tkinter n'est pas disponible : {exc}")
try:
    import cv2  # noqa: F401
except Exception as exc:
    raise SystemExit(f"OpenCV n'est pas disponible dans le venv : {exc}. Relancez ./setup.sh")
PY

exec "$VENV_PY" main.py "$@"
