#!/usr/bin/env bash
# Keep the sweep going until every run is scored. Variant names given as
# arguments narrow it to those; with none it runs the whole plan.
#
# The sweep dies for reasons that are not its fault: the machine runs short of
# memory, ComfyUI falls over, a session ends and takes the process group. Each
# is survivable because the sweep resumes from the runs' own score.json - what
# was missing is something to start it again.
set -uo pipefail
cd "$(dirname "$0")/.."
PY=./ComfyUI/.venv/bin/python
LOG=var/logs/sweep.log

while true; do
  left=$(WANT="$*" $PY - <<'PLAN'
import os, sys; sys.path.insert(0, '.')
from tools.sweep import plan, done
d, want = done(), set(os.environ.get("WANT", "").split())
print(len([j for j in plan()
           if f"{j[0]}_{j[1]}" not in d and (not want or j[1] in want)]))
PLAN
)
  if [ "${left:-0}" -eq 0 ]; then
    echo "$(date '+%H:%M:%S') sweep complete" >> $LOG
    $PY -u tools/sweep.py report >> $LOG 2>&1
    exit 0
  fi

  # The sweep waits for ComfyUI but cannot start it, so it waited twenty
  # minutes for something nobody was going to fix.
  if ! curl -s -m 5 -o /dev/null http://127.0.0.1:8188/system_stats; then
    echo "$(date '+%H:%M:%S') supervisor: ComfyUI is down, starting it" >> $LOG
    $PY tools/detach.py ./start.sh > var/logs/comfy-start.log 2>&1 < /dev/null
    sleep 40
  fi

  echo "$(date '+%H:%M:%S') supervisor: $left left${*:+ of $*}, starting" >> $LOG
  $PY -u tools/sweep.py run "$@" >> $LOG 2>&1 &
  sweep=$!

  # Watch rather than block: a sweep that dies mid-run should be noticed in
  # seconds, not whenever the next thing happens to look.
  while kill -0 $sweep 2>/dev/null; do
    sleep 30
    if ! curl -s -m 5 -o /dev/null http://127.0.0.1:8188/system_stats; then
      echo "$(date '+%H:%M:%S') supervisor: ComfyUI went down mid-sweep" >> $LOG
      $PY tools/detach.py ./start.sh > var/logs/comfy-start.log 2>&1 < /dev/null
      sleep 40
    fi
  done
  wait $sweep 2>/dev/null
  echo "$(date '+%H:%M:%S') supervisor: sweep exited, waiting 60s" >> $LOG
  sleep 60
done
