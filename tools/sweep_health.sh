#!/usr/bin/env bash
# Is the sweep actually making progress? Prints one line and exits 0 healthy.
#
# Aliveness is not progress: a supervisor can be up with a sweep that is
# waiting on a service nobody will start, and a scored count that has not moved
# in an hour says so where `pgrep` does not.
set -uo pipefail
cd "$(dirname "$0")/.."
PY=./ComfyUI/.venv/bin/python
MARK=var/sweep.progress

scored=$(ls out/runs/*/score.json 2>/dev/null | wc -l | tr -d ' ')
sup=$(pgrep -f keep_sweeping >/dev/null && echo up || echo down)
comfy=$(curl -s -m 5 -o /dev/null -w '%{http_code}' http://127.0.0.1:8188/system_stats 2>/dev/null)
gen=$(pgrep -f "run.py.*configs/sweep" >/dev/null && echo yes || echo no)

was=$(cat $MARK 2>/dev/null || echo -1)
echo "$scored" > $MARK

state=healthy
[ "$sup" = down ] && state=supervisor-down
[ "$comfy" != 200 ] && state=comfy-down
[ "$scored" -eq "$was" ] && [ "$gen" = no ] && state=stalled

echo "$(date '+%F %H:%M') scored $scored/96  supervisor $sup  comfy $comfy  generating $gen  -> $state"
[ "$state" = healthy ] && exit 0

if [ "$sup" = down ]; then
  # Restarting without the variants it was narrowed to would quietly widen the
  # sweep back to the whole plan.
  arms=$(cat var/sweep.arms 2>/dev/null || true)
  nohup ./tools/keep_sweeping.sh $arms > /dev/null 2>&1 < /dev/null &
  echo "  restarted the supervisor${arms:+ on $arms}"
fi
exit 1
