#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
FRONTEND_DIR="${SCRIPT_DIR}/frontend"
BACKEND_REQS="${SCRIPT_DIR}/backend/requirements.txt"
BACKEND_VENV="${SCRIPT_DIR}/backend/.venv"
BACKEND_PYTHON="${BACKEND_VENV}/bin/python"

if ! command -v python3 >/dev/null 2>&1; then
  printf 'python3 is required to run demo backend.\n' >&2
  exit 1
fi

if ! command -v bun >/dev/null 2>&1; then
  printf 'bun is required to run demo frontend.\n' >&2
  exit 1
fi

if [ ! -d "${FRONTEND_DIR}/node_modules" ]; then
  printf '[demo] installing frontend dependencies...\n'
  bun --cwd "${FRONTEND_DIR}" install
fi

printf '[demo] ensuring backend python dependencies...\n'
if [ ! -x "${BACKEND_PYTHON}" ]; then
  python3 -m venv "${BACKEND_VENV}"
fi
(
  cd "${SCRIPT_DIR}/backend"
  "${BACKEND_PYTHON}" -m pip install -r "${BACKEND_REQS}" >/dev/null
)

BACKEND_PID=""
FRONTEND_PID=""

cleanup() {
  set +e
  if [ -n "${FRONTEND_PID}" ] && kill -0 "${FRONTEND_PID}" >/dev/null 2>&1; then
    kill "${FRONTEND_PID}" >/dev/null 2>&1 || true
  fi
  if [ -n "${BACKEND_PID}" ] && kill -0 "${BACKEND_PID}" >/dev/null 2>&1; then
    kill "${BACKEND_PID}" >/dev/null 2>&1 || true
  fi
  wait >/dev/null 2>&1 || true
}

trap cleanup EXIT INT TERM

printf '[demo] starting backend on http://127.0.0.1:8011\n'
(
  cd "${REPO_ROOT}"
  "${BACKEND_PYTHON}" "${SCRIPT_DIR}/backend/run.py"
) &
BACKEND_PID="$!"

printf '[demo] starting frontend on http://127.0.0.1:4173\n'
(
  cd "${FRONTEND_DIR}"
  bun run dev --host 0.0.0.0 --port 4173 --strictPort
) &
FRONTEND_PID="$!"

set +e
wait -n "${BACKEND_PID}" "${FRONTEND_PID}"
STATUS="$?"
set -e

exit "${STATUS}"
