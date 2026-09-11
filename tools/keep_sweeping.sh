#!/usr/bin/env bash
# Keep the sweep going until every run is scored.
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
  left=$($PY - <<'PLAN'
import sys; sys.path.insert(0, '.')
from tools.sweep import plan, done
d = done()
print(len([j for j in plan() if f"{j[0]}_{j[1]}" not in d]))
PLAN
)
  if [ "${left:-0}" -eq 0 ]; then
    echo "$(date '+%H:%M:%S') sweep complete" >> $LOG
    $PY -u tools/sweep.py report >> $LOG 2>&1
    exit 0
  fi

  echo "$(date '+%H:%M:%S') supervisor: $left left, starting" >> $LOG
  $PY -u tools/sweep.py run >> $LOG 2>&1
  echo "$(date '+%H:%M:%S') supervisor: sweep exited, waiting 60s" >> $LOG
  sleep 60
done
