#!/usr/bin/env bash
# Service control for the sprite pipeline, driven by the Makefile: up|down|restart|status|logs.

set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="$ROOT/ComfyUI/.venv/bin/python"
RUN_DIR="$ROOT/.run"
LOG_DIR="$ROOT/var/logs"

COMFY_PORT="${COMFY_PORT:-8188}"
UI_PORT="${UI_PORT:-8000}"
OLLAMA_PORT="${OLLAMA_PORT:-11434}"

# ComfyUI imports torch and scans the model folders, so it is the slow one.
COMFY_TIMEOUT="${COMFY_TIMEOUT:-180}"
UI_TIMEOUT="${UI_TIMEOUT:-30}"
OLLAMA_TIMEOUT="${OLLAMA_TIMEOUT:-60}"

# Empty on purpose: --gpu-only measured ~5x SLOWER, because it forbids offloading and macOS swaps.
VRAM_MODE="${VRAM_MODE:-}"

# Launcher concerns: these must be set before ComfyUI imports torch. An environment variable still wins.
read_compute() {
  [[ -f "$ROOT/library/configs/_global.yaml" ]] || return 0
  local line
  while IFS='=' read -r key value; do
    [[ -n "$key" ]] || continue
    case "$key" in
      vram_mode)          VRAM_MODE="${VRAM_MODE:-$value}" ;;
      torch_threads)      export OMP_NUM_THREADS="${OMP_NUM_THREADS:-$value}"
                          export MKL_NUM_THREADS="${MKL_NUM_THREADS:-$value}" ;;
      # The MPS allocator rejects low > high and low defaults to 1.4, so pin low alongside high.
      mps_high_watermark) export PYTORCH_MPS_HIGH_WATERMARK_RATIO="${PYTORCH_MPS_HIGH_WATERMARK_RATIO:-$value}"
                          export PYTORCH_MPS_LOW_WATERMARK_RATIO="${PYTORCH_MPS_LOW_WATERMARK_RATIO:-$value}" ;;
      mps_low_watermark)  export PYTORCH_MPS_LOW_WATERMARK_RATIO="${PYTORCH_MPS_LOW_WATERMARK_RATIO:-$value}" ;;
    esac
  done < <("$PY" - "$ROOT/library/configs/_global.yaml" <<'EOF'
import sys, yaml
try:
    cfg = yaml.safe_load(open(sys.argv[1])) or {}
except Exception:
    sys.exit(0)
for k, v in (cfg.get("compute") or {}).items():
    if v not in (None, ""):
        print(f"{k}={v}")
EOF
)
}
read_compute

if [[ -t 1 ]]; then
  G=$'\033[32m'; R=$'\033[31m'; D=$'\033[2m'; B=$'\033[1m'; O=$'\033[0m'; W=$'\033[33m'
else
  G=""; R=""; D=""; B=""; O=""; W=""
fi

url_for() {
  case "$1" in
    comfy)  echo "http://127.0.0.1:$COMFY_PORT/system_stats" ;;
    ollama) echo "http://127.0.0.1:$OLLAMA_PORT/api/tags" ;;
    ui)     echo "http://127.0.0.1:$UI_PORT/api/configs" ;;
  esac
}

home_for() {
  case "$1" in
    comfy)  echo "http://127.0.0.1:$COMFY_PORT" ;;
    ollama) echo "http://127.0.0.1:$OLLAMA_PORT" ;;
    ui)     echo "http://127.0.0.1:$UI_PORT" ;;
  esac
}

healthy() { curl -sf -m 2 "$(url_for "$1")" >/dev/null 2>&1; }

# ------------------------------------------------------------------- start

start_one() {
  local name="$1"; shift
  if healthy "$name"; then
    printf "  %sfound %s already running%s\n" "$D" "$name" "$O"
    return 0
  fi
  printf "  starting %s…\n" "$name"
  mkdir -p "$RUN_DIR" "$LOG_DIR"
  # Job control puts each background job in its own group so `down` signals the tree; macOS has no setsid.
  set -m
  nohup "$@" > "$LOG_DIR/$name.log" 2>&1 &
  local pid=$!
  set +m
  echo "$pid" > "$RUN_DIR/$name.pid"
}

start_all() {
  ( cd "$ROOT/ComfyUI" && PYTORCH_ENABLE_MPS_FALLBACK=1 \
    start_one comfy "$PY" main.py --use-pytorch-cross-attention \
      ${VRAM_MODE:+$VRAM_MODE} --listen 127.0.0.1 --port "$COMFY_PORT" )

  if command -v ollama >/dev/null 2>&1; then
    start_one ollama ollama serve
  else
    printf "  %sollama not installed — skipping (only needed for LLM poses)%s\n" "$D" "$O"
  fi

  PORT="$UI_PORT" start_one ui "$PY" "$ROOT/server.py"
}

wait_one() {
  local name="$1" timeout="$2" i
  if [[ "$name" == "ollama" ]] && ! command -v ollama >/dev/null 2>&1; then
    return 0
  fi
  printf "  waiting for %-7s" "$name"
  for ((i = 0; i < timeout; i++)); do
    if healthy "$name"; then printf " %sok%s\n" "$G" "$O"; return 0; fi
    printf "."
    sleep 1
  done
  printf " %stimeout after %ss%s\n" "$R" "$timeout" "$O"
  printf "  last lines of var/logs/%s.log:\n" "$name"
  tail -n 12 "$LOG_DIR/$name.log" 2>/dev/null | sed 's/^/    /'
  return 1
}

cmd_up() {
  start_all
  local rc=0
  wait_one comfy  "$COMFY_TIMEOUT"  || rc=1
  wait_one ollama "$OLLAMA_TIMEOUT" || rc=1
  wait_one ui     "$UI_TIMEOUT"     || rc=1
  echo
  if [[ $rc -ne 0 ]]; then
    printf "  %ssome services failed to start%s — see: make logs\n\n" "$R" "$O"
    return 1
  fi
  printf "  %s●%s ComfyUI   %s\n"        "$G" "$O" "$(home_for comfy)"
  command -v ollama >/dev/null 2>&1 &&
    printf "  %s●%s Ollama    %s\n"      "$G" "$O" "$(home_for ollama)"
  printf "  %s●%s %sWeb UI    %s%s\n"    "$G" "$O" "$B" "$(home_for ui)" "$O"
  echo
  printf "  %slogs: make logs   ·   stop: make down%s\n\n" "$D" "$O"
}

# -------------------------------------------------------------------- stop

stop_one() {
  local name="$1" pidfile="$RUN_DIR/$1.pid" pid i
  [[ -f "$pidfile" ]] || return 1
  pid="$(cat "$pidfile")"
  if ! kill -0 "$pid" 2>/dev/null; then rm -f "$pidfile"; return 1; fi

  kill -TERM -- "-$pid" 2>/dev/null || kill -TERM "$pid" 2>/dev/null
  for ((i = 0; i < 40; i++)); do
    kill -0 "$pid" 2>/dev/null || break
    sleep 0.25
  done
  if kill -0 "$pid" 2>/dev/null; then
    kill -KILL -- "-$pid" 2>/dev/null || kill -KILL "$pid" 2>/dev/null
  fi
  rm -f "$pidfile"
  printf "  stopped %-7s %s(pid %s)%s\n" "$name" "$D" "$pid" "$O"
  return 0
}

# A restart bounces ComfyUI, and an in-flight run then dies with ECONNREFUSED blaming the network.
runs_in_flight() {
  # Ask ComfyUI's queue, not run.log: run.log exists only when the server launched the run.
  local body
  body=$(curl -s -m 2 "http://127.0.0.1:${COMFY_PORT}/queue" 2>/dev/null) || return 1
  [[ -n "$body" ]] || return 1
  "$PY" - "$body" <<'EOF'
import json, sys
try:
    q = json.loads(sys.argv[1])
except Exception:
    sys.exit(1)
busy = len(q.get("queue_running") or []) + len(q.get("queue_pending") or [])
sys.exit(0 if busy else 1)
EOF
}

warn_if_busy() {
  if runs_in_flight; then
    printf "  %s! ComfyUI has work in its queue right now%s\n" "$W" "$O"
    printf "  %s  restarting bounces ComfyUI and that run will die with%s\n" "$D" "$O"
    printf "  %s  'connection refused'. Use 'make down' deliberately, or wait.%s\n" "$D" "$O"
    if [[ "${FORCE:-}" != "1" ]]; then
      printf "  %s  refusing; re-run with FORCE=1 to override.%s\n" "$D" "$O"
      return 1
    fi
  fi
  return 0
}

cmd_down() {
  local any=1
  # UI first so it stops polling, then the workers.
  for name in ui comfy ollama; do
    stop_one "$name" && any=0
  done
  if [[ $any -ne 0 ]]; then
    printf "  %snothing to stop%s\n" "$D" "$O"
    for name in comfy ollama ui; do
      if healthy "$name"; then
        printf "  %snote: %s is up but was not started by make — leaving it alone%s\n" \
          "$D" "$name" "$O"
      fi
    done
  fi
}

# ------------------------------------------------------------------ status

cmd_status() {
  printf "  %-8s %-28s %s\n" SERVICE URL "STATE"
  for name in comfy ollama ui; do
    local pid=""
    [[ -f "$RUN_DIR/$name.pid" ]] && pid=" (pid $(cat "$RUN_DIR/$name.pid"))"
    # Colour codes are printed outside the padded fields; %-Ns counts escape bytes as width.
    printf "  %-8s %-28s " "$name" "$(home_for "$name")"
    if healthy "$name"; then
      printf "%sup%s%s\n" "$G" "$O" "$pid"
    else
      printf "%sdown%s\n" "$R" "$O"
    fi
  done
  echo
  # Match the python process itself: a bare -f search also hits any shell mentioning run.py.
  local running
  running="$(pgrep -fl 'run\.py .*configs/' 2>/dev/null \
             | grep -vE '^[0-9]+ +/bin/(z|ba)?sh' \
             | grep -oE 'configs/[A-Za-z0-9_.-]+\.yaml' | head -1)"
  if [[ -n "$running" ]]; then
    printf "  %spipeline running:%s %s\n" "$B" "$O" "$running"
  else
    printf "  %sno pipeline running%s\n" "$D" "$O"
  fi
  local latest
  latest="$(ls -dt "$ROOT"/out/runs/*/ 2>/dev/null | head -1)"
  [[ -n "$latest" ]] && printf "  %slatest run: %s%s\n" "$D" "$(basename "$latest")" "$O"
}

cmd_logs() {
  mkdir -p "$LOG_DIR"
  shopt -s nullglob
  local files=("$LOG_DIR"/*.log)
  if [[ ${#files[@]} -eq 0 ]]; then
    printf "  %sno logs yet — run: make up%s\n" "$D" "$O"; return 0
  fi
  tail -n 40 -f "${files[@]}"
}

case "${1:-status}" in
  up)      cmd_up ;;
  down)    warn_if_busy || exit 1; cmd_down ;;
  restart) warn_if_busy || exit 1; cmd_down; echo; cmd_up ;;
  status)  cmd_status ;;
  logs)    cmd_logs ;;
  *)       echo "usage: ctl.sh {up|down|restart|status|logs}" >&2; exit 2 ;;
esac
