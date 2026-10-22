#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -lt 1 ]; then
  echo "Usage: $0 <replay_path>" >&2
  exit 2
fi

REPLAY_PATH="$1"
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
ENV_FILE="${PROJECT_ROOT}/.env"

if [ ! -f "${REPLAY_PATH}" ]; then
  echo "Replay file not found: ${REPLAY_PATH}" >&2
  exit 1
fi

REPLAY_PATH_WIN="${REPLAY_PATH}"
if command -v wslpath >/dev/null 2>&1; then
  REPLAY_PATH_WIN="$(wslpath -w "${REPLAY_PATH}")"
fi

if [ -f "${ENV_FILE}" ]; then
  set -a
  # shellcheck source=/dev/null
  . "${ENV_FILE}"
  set +a
fi

if [ -n "${LEAGUE_REPLAY_OPEN_CMD_TEMPLATE:-}" ]; then
  CMD="${LEAGUE_REPLAY_OPEN_CMD_TEMPLATE//\{replay_path\}/${REPLAY_PATH}}"
  CMD="${CMD//\{replay_path_win\}/${REPLAY_PATH_WIN}}"
  bash -lc "${CMD}"
  echo "Triggered custom replay load command"
  exit 0
fi

# WSL default: use Windows file association for .rofl (cmd start tends to be more robust)
if command -v cmd.exe >/dev/null 2>&1; then
  cmd.exe /C start "" "${REPLAY_PATH_WIN}" >/dev/null 2>&1 && {
    echo "Triggered replay open via cmd.exe start"
    exit 0
  }
fi

if command -v powershell.exe >/dev/null 2>&1; then
  powershell.exe -NoProfile -Command "Start-Process -LiteralPath '${REPLAY_PATH_WIN}'" >/dev/null 2>&1 && {
    echo "Triggered replay open via powershell.exe"
    exit 0
  }
fi

if command -v xdg-open >/dev/null 2>&1; then
  xdg-open "${REPLAY_PATH}" >/dev/null 2>&1 &
  echo "Triggered replay open via xdg-open"
  exit 0
fi

if command -v gio >/dev/null 2>&1; then
  gio open "${REPLAY_PATH}" >/dev/null 2>&1 &
  echo "Triggered replay open via gio open"
  exit 0
fi

echo "No replay loader found. Set LEAGUE_REPLAY_OPEN_CMD_TEMPLATE in .env." >&2
exit 1
