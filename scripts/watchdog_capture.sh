#!/usr/bin/env bash
# Collect the evidence a WindowServer watchdog timeout leaves behind.
#
# The reports name the process that died, never the forty seconds before it.
# That window only exists in the unified log, it is gone by the time anyone
# thinks to ask, and reconstructing the timestamps by hand is what went wrong
# the first time. So the timestamps come from the report's own filename.
#
#   watchdog_capture.sh            the newest event
#   watchdog_capture.sh --list     what events exist
#   watchdog_capture.sh --watch    wait for the next one, then capture it
#
# Needs `sudo log config --mode "persist:info"` to have been set BEFORE the
# event. Without it the log window comes back empty and only the stackshot
# summary is worth reading.

set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REPORTS="/Library/Logs/DiagnosticReports"
OUT_DIR="${OUT_DIR:-$ROOT/var/logs/watchdog}"
BEFORE_MIN="${BEFORE_MIN:-3}"
AFTER_MIN="${AFTER_MIN:-1}"
POLL_S="${POLL_S:-60}"

newest() {
  ls -t "$REPORTS"/*userspace_watchdog_timeout.spin 2>/dev/null | head -1
}

# WindowServer_2026-09-10-093203_host.userspace_watchdog_timeout.spin
stamp_of() {
  basename "$1" | sed -nE 's/.*_([0-9]{4})-([0-9]{2})-([0-9]{2})-([0-9]{2})([0-9]{2})([0-9]{2})_.*/\1-\2-\3 \4:\5:\6/p'
}

shift_time() {
  date -j -v"$2"M -f "%Y-%m-%d %H:%M:%S" "$1" "+%Y-%m-%d %H:%M:%S"
}

capture() {
  local spin="$1" stamp start end out
  stamp="$(stamp_of "$spin")"
  if [[ -z "$stamp" ]]; then
    echo "could not read a timestamp out of $(basename "$spin")" >&2
    return 1
  fi
  start="$(shift_time "$stamp" "-$BEFORE_MIN")"
  end="$(shift_time "$stamp" "+$AFTER_MIN")"
  mkdir -p "$OUT_DIR"
  out="$OUT_DIR/${stamp//[: ]/-}"

  echo "event    $stamp"
  echo "window   $start -> $end"

  # The stackshot answers "was anything actually running", which is what
  # separates a machine under load from a machine that simply stopped
  # answering. Both have looked like a freeze from the outside.
  {
    grep -m1 "^Reason:" "$spin"
    grep -m1 "^Total CPU Time:" "$spin"
    grep -m1 "^Time Since Fork:" "$spin"
    echo
    echo "heaviest processes during the sample:"
    awk '/^Process: /{p=$0; sub(/^Process: +/,"",p); c=""}
         / *CPU Time: /{if(p!=""&&c==""){c=$3; print c"\t"p; p=""}}' "$spin" \
      | sed 's/s\t/\t/' | sort -rn | head -15
  } > "$out.summary.txt" 2>&1
  echo "wrote    $out.summary.txt"

  log show --start "$start" --end "$end" --info --debug --style compact \
    > "$out.log.txt" 2>&1
  local lines
  lines="$(wc -l < "$out.log.txt" | tr -d ' ')"
  if [[ "$lines" -lt 10 ]]; then
    echo "wrote    $out.log.txt ($lines lines)"
    echo "         empty - logging was not persisting when this happened, and a"
    echo "         reboot clears what was never written down."
    echo "         sudo log config --mode \"persist:info\""
    return 0
  fi

  # Four minutes of everything is ~80 MB and nobody reads it. The lines that
  # said something last time were WindowServer's own errors and the second the
  # log rate changed, so those are extracted and the rest is compressed.
  # Everything after the kill is thousands of apps reconnecting, which says
  # nothing about why. The minutes BEFORE it are the whole point.
  local clock="${stamp#* }"
  {
    echo "--- WindowServer / watchdogd errors, up to the kill at $clock ---"
    grep -E "WindowServer|watchdogd" "$out.log.txt" \
      | awk -v t="$clock" '($3=="E"||$3=="F") && $2<t'
    echo
    echo "--- unresponsive clients and timed-out transactions, before the kill ---"
    grep -E "connectionIsUnres|transaction .* timed out|synchronize timed out" \
      "$out.log.txt" | awk -v t="$clock" '$2<t'
    echo
    echo "--- log lines per second (a stalled machine shows up as a gap) ---"
    awk '{print substr($2,1,8)}' "$out.log.txt" | LC_ALL=C sort | uniq -c
  } > "$out.signal.txt" 2>&1
  echo "wrote    $out.signal.txt   <- read this one"

  gzip -f "$out.log.txt"
  echo "wrote    $out.log.txt.gz ($lines lines, full detail)"

  sysctl -n kern.memorystatus_vm_pressure_level vm.swapusage > "$out.memory.txt" 2>&1
  echo "wrote    $out.memory.txt"
}

case "${1:-}" in
  --list)
    ls -lt "$REPORTS"/*userspace_watchdog_timeout.spin 2>/dev/null \
      | awk '{print $6, $7, $8, $9}' || echo "no watchdog events recorded"
    ;;
  --watch)
    seen="$(newest)"
    echo "watching for a watchdog event; newest so far: $(basename "${seen:-none}")"
    while sleep "$POLL_S"; do
      current="$(newest)"
      if [[ -n "$current" && "$current" != "$seen" ]]; then
        echo
        echo "new event: $(basename "$current")"
        capture "$current"
        seen="$current"
      fi
    done
    ;;
  --help|-h)
    sed -n '2,16p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
    ;;
  *)
    spin="$(newest)"
    if [[ -z "$spin" ]]; then
      echo "no watchdog events recorded - nothing to capture"
      exit 0
    fi
    capture "$spin"
    ;;
esac
