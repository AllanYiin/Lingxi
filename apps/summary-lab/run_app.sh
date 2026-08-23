#!/usr/bin/env bash
set -u

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

echo "========================================="
echo "  One-click install / run (stable)"
echo "========================================="
echo

PY=""
if command -v python3 >/dev/null 2>&1; then PY="python3"; fi
if [ -z "$PY" ] && command -v python >/dev/null 2>&1; then PY="python"; fi
if [ -z "$PY" ]; then
  echo "[ERROR] Python not found. Please install Python 3.10+ first."
  printf "Press Enter to close..."
  read -r _
  exit 1
fi

if ! command -v node >/dev/null 2>&1; then
  echo "[ERROR] Node.js not found. Install Node.js 20 or newer: https://nodejs.org/"
  exit 1
fi
if ! command -v npm >/dev/null 2>&1; then
  echo "[ERROR] npm not found. Reinstall Node.js from https://nodejs.org/"
  exit 1
fi
if ! command -v cargo >/dev/null 2>&1; then
  echo "[ERROR] Cargo not found. Install Rust stable: https://rustup.rs/"
  exit 1
fi

mkdir -p "$ROOT/logs"
OS_NAME="$(uname -s 2>/dev/null || echo unknown)"
IS_WSL="0"
if [ -n "${WSL_INTEROP:-}" ] || grep -qi microsoft /proc/sys/kernel/osrelease 2>/dev/null; then
  IS_WSL="1"
fi
echo "[INFO] POSIX shell launcher mode (.sh)."
if [ "$OS_NAME" = "Darwin" ]; then
  echo "[INFO] On macOS, you can also use run_app.command for Finder double-click."
fi
printf '[INFO] POSIX launcher started via run_app.sh\n' >>"$ROOT/logs/launcher.log"
echo


log_warn() {
  printf '[WARN] %s\n' "$1"
}

open_url() {
  url="$1"
  label="${2:-URL}"
  if [ -z "$url" ]; then
    return 0
  fi
  if [ "$IS_WSL" = "1" ]; then
    if command -v powershell.exe >/dev/null 2>&1; then
      powershell.exe -NoProfile -Command "Start-Process '$url'" >>"$ROOT/logs/open.log" 2>&1 && echo "[INFO] Opened $label in Windows browser via powershell.exe." && return 0
    fi
    if command -v cmd.exe >/dev/null 2>&1; then
      cmd.exe /C start "" "$url" >>"$ROOT/logs/open.log" 2>&1 && echo "[INFO] Opened $label in Windows browser via cmd.exe." && return 0
    fi
  fi
  if [ "$OS_NAME" = "Darwin" ] && command -v open >/dev/null 2>&1; then
    open "$url" >>"$ROOT/logs/open.log" 2>&1 && echo "[INFO] Opened $label via macOS open." && return 0
  fi
  if command -v xdg-open >/dev/null 2>&1; then
    xdg-open "$url" >>"$ROOT/logs/open.log" 2>&1 && echo "[INFO] Opened $label via xdg-open." && return 0
  fi
  "$PYEXE" -m webbrowser "$url" >>"$ROOT/logs/open.log" 2>&1 && echo "[INFO] Opened $label via python -m webbrowser." && return 0
  printf '[WARN] Failed to open %s automatically: %s\n' "$label" "$url" >>"$ROOT/logs/open.log"
  echo "[WARN] Failed to open $label automatically. Please open this URL manually: $url"
  return 1
}

probe_url() {
  url="$1"
  label="${2:-URL}"
  if [ -z "$url" ]; then
    return 0
  fi
  if "$PYEXE" - "$url" "$label" >>"$ROOT/logs/open.log" 2>&1 <<'PY'
import sys
import time
import urllib.request

url = sys.argv[1]
label = sys.argv[2]
deadline = time.time() + 4.0
last_error = None
while time.time() < deadline:
    try:
        with urllib.request.urlopen(url, timeout=1.5) as response:
            status = getattr(response, "status", "unknown")
            print(f"[READY] {label} {url} status={status}")
            raise SystemExit(0)
    except Exception as exc:  # pragma: no cover - emitted into shell log
        last_error = exc
        time.sleep(0.5)

print(f"[WARN] {label} not ready before browser open: {url} last_error={last_error}")
raise SystemExit(1)
PY
  then
    echo "[INFO] $label responded before browser open: $url"
    return 0
  fi
  echo "[WARN] $label may not be ready yet. Browser will still open: $url"
  return 1
}

VENV_DIR=".venv"
if [ ! -x "$ROOT/$VENV_DIR/bin/python" ]; then
  echo "[1/6] Creating venv ($VENV_DIR) if needed..."
  "$PY" -m venv "$ROOT/$VENV_DIR" >>"$ROOT/logs/bootstrap.log" 2>&1 || log_warn "Failed to create venv. Falling back to system Python."
fi

PYEXE="$ROOT/$VENV_DIR/bin/python"
if [ ! -x "$PYEXE" ]; then
  PYEXE="$PY"
fi

echo "[2/6] Auto-fix + install dependencies..."
"$PYEXE" "scripts/project_launcher.py" --root "$ROOT" --venv ".venv" --ensure-only >>"$ROOT/logs/ensure.log" 2>&1 || log_warn "Auto-fix or install step reported issues. See logs/ensure.log."

BACKEND_PID=""
FRONTEND_PID=""
APP_PID=""
GUI_PID=""
cleanup() {
  set +e
  [ -n "${GUI_PID:-}" ] && kill "${GUI_PID}" >/dev/null 2>&1 || true
  [ -n "${FRONTEND_PID:-}" ] && kill "${FRONTEND_PID}" >/dev/null 2>&1 || true
  [ -n "${BACKEND_PID:-}" ] && kill "${BACKEND_PID}" >/dev/null 2>&1 || true
  [ -n "${APP_PID:-}" ] && kill "${APP_PID}" >/dev/null 2>&1 || true
}
trap cleanup EXIT INT TERM

echo "[3/6] Starting backend or local app..."
echo "[INFO] Backend not detected. Skipping backend startup."


echo "[4/6] Starting frontend (if any)..."
start_frontend() {
  if ! cd "$ROOT/."; then
    printf '[WARN] Frontend cd failed: %s\n' "$ROOT/." >>"$ROOT/logs/frontend.log"
    return 1
  fi
  if ! npm install >>"$ROOT/logs/frontend.log" 2>&1; then
    printf '[WARN] Frontend install failed in %s\n' "$ROOT/." >>"$ROOT/logs/frontend.log"
  fi
  if ! npm run dev >>"$ROOT/logs/frontend.log" 2>&1; then
    printf '[WARN] Frontend start failed in %s\n' "$ROOT/." >>"$ROOT/logs/frontend.log"
  fi
}
start_frontend &
FRONTEND_PID=$!


echo "[5/6] Opening browser URLs..."
sleep 1
if "$PYEXE" "$ROOT/scripts/wait_for_http.py" "http://127.0.0.1:4174/api/health" 30 "lingxi-summary-lab" >>"$ROOT/logs/launcher.log" 2>&1; then
  open_url "http://127.0.0.1:4174" "Summary Lab"
else
  echo "[ERROR] Summary Lab did not become ready. See logs/frontend.log and logs/launcher.log."
fi


echo "[6/6] Logs directory: $ROOT/logs"
if [ -z "${BACKEND_PID:-}${FRONTEND_PID:-}${APP_PID:-}${GUI_PID:-}" ]; then
  echo "[WARN] No process was started. Check logs for details."
  printf "Press Enter to close..."
  read -r _
  exit 0
fi

echo "Started. Press Ctrl+C to stop."
wait
printf "Press Enter to close..."
read -r _
