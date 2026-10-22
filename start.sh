#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

if [[ "${1:-}" == "doctor" ]]; then
  shift
  exec python3.12 scripts/doctor.py "$@"
fi

if [[ ! -x .venv/bin/python ]] || ! .venv/bin/python -c 'import sys; assert sys.version_info >= (3, 10)' >/dev/null 2>&1; then
  if [[ -e .venv ]]; then
    backup=".venv.invalid.$(date +%Y%m%d%H%M%S)"
    mv .venv "$backup"
    echo "Ancien environnement invalide conserve dans $backup"
  fi
  command -v python3.12 >/dev/null || { echo "Python 3.12 est requis (python3.12 introuvable)." >&2; exit 1; }
  python3.12 -m venv .venv
fi

if ! .venv/bin/python -c 'import lol_search, streamlit, numpy, pandas, plotly, sklearn, cv2' >/dev/null 2>&1; then
  .venv/bin/python -m pip install -e '.[video]'
fi

case "${1:-app}" in
  setup)
    echo "Installation prête. Lancez ./start.sh"
    ;;
  app)
    shift || true
    exec .venv/bin/python -m streamlit run streamlit_app.py --server.address 127.0.0.1 "$@"
    ;;
  cli)
    shift
    exec .venv/bin/python -m lol_search.cli "$@"
    ;;
  *)
    echo "Usage: ./start.sh [app|setup|doctor|cli ...]" >&2
    exit 2
    ;;
esac
