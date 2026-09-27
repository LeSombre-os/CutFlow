#!/bin/bash
# Lance CutFlow Studio (clé USB exFAT : venv sur le disque système, pas de .venv local).
# Si le serveur tourne déjà, ouvre juste le navigateur dessus.
VENV="$HOME/.venvs/CutFlow/bin/python"
DIR="$(dirname "$(readlink -f "$0")")/.."
PORT=8001
PREV=""
for a in "$@"; do
  if [ "$PREV" = "--port" ]; then PORT="$a"; fi
  case "$a" in --port=*) PORT="${a#--port=}";; esac
  PREV="$a"
done
URL="http://127.0.0.1:$PORT"

open_browser() {
  case " $* " in
    *" --no-browser "*) return 0;;
  esac
  if command -v xdg-open >/dev/null 2>&1; then
    xdg-open "$URL" >/dev/null 2>&1 &
  elif command -v gio >/dev/null 2>&1; then
    gio open "$URL" >/dev/null 2>&1 &
  fi
}

if curl -sf -m 2 "$URL/api/health" >/dev/null 2>&1; then
  echo "CutFlow Studio tourne déjà — ouverture de $URL"
  open_browser "$@"
  exit 0
fi

if [ ! -x "$VENV" ]; then
  echo "ERREUR : venv introuvable : $VENV"
  echo "Crée-le : python3 -m venv ~/.venvs/CutFlow && ~/.venvs/CutFlow/bin/pip install \"$DIR\"/."
  read -p "Appuie sur Entrée pour fermer…" -r
  exit 1
fi
exec "$VENV" "$DIR/studio/app.py" "$@"
