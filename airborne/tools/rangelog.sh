#!/usr/bin/env bash
# Log what the DRONE hears from each associated ground station, once a second.
#
#   ./tools/rangelog.sh              # run until stopped, writes /tmp/rangelog-*.csv
#   SECONDS_MAX=600 ./tools/rangelog.sh
#
# Run on the DRONE Jetson, as the companion to ground-side tools/linkcheck.sh.
#
# Why this exists: linkcheck.sh logs what the GROUND hears from the drone, which
# is the strong half of an asymmetric link. The control-link failsafe on
# 2026-08-06 happened because the drone could not hear the ground, and that number
# only exists here, in the AP's station table. A range test measuring only the
# ground side cannot see the failure mode it is meant to characterise.
#
# It writes to a local file and never needs the link, so it keeps recording
# through exactly the degradation being measured. Start it, walk the aircraft out,
# come back, stop it, then collect the file.
#
# This driver reports only inactive time and signal per station, no bitrate, so
# there is no rate early-warning from this side. Watch inactive_ms climbing: it is
# how long since the AP last heard anything from that station, and it going up is
# the direct precursor of the control-link failsafe.
#
# Alignment: this machine's clock is unreliable (no RTC, no NTP on an isolated
# hotspot, observed weeks behind). Align with the ground-side CSV using the
# elapsed column, not wall clock.
set -uo pipefail

AP="${AP:-}"
if [ -z "$AP" ]; then
  for i in /sys/class/net/wl*; do
    d=$(basename "$i")
    if iw dev "$d" info 2>/dev/null | grep -q "type AP"; then AP="$d"; break; fi
  done
fi
[ -n "$AP" ] || { echo "no AP-mode wireless interface found; set AP=<iface>" >&2; exit 1; }

LOG="${LOG:-/tmp/rangelog-$(date +%H%M%S).csv}"
MAX="${SECONDS_MAX:-0}"

echo "logging to $LOG   (ap $AP)"
echo "elapsed,wallclock,station,signal_dbm,inactive_ms" > "$LOG"
printf '%-8s %-18s %8s %10s\n' ELAPSED STATION SIGNAL INACTIVE

start=$(date +%s)
while true; do
  now=$(date +%s)
  elapsed=$(( now - start ))
  if [ "$MAX" -gt 0 ] && [ "$elapsed" -ge "$MAX" ]; then break; fi

  iw dev "$AP" station dump 2>/dev/null | awk -v el="$elapsed" -v wc="$(date +%H:%M:%S)" '
    /^Station/ {
      if (mac != "") print el "," wc "," mac "," sig "," inact
      mac = $2; sig = ""; inact = ""
    }
    /inactive time:/ { inact = $3 }
    /signal:/        { sig = $2 }
    END { if (mac != "") print el "," wc "," mac "," sig "," inact }
  ' | tee -a "$LOG" | while IFS=, read -r el wc mac sig inact; do
        warn=""
        if [ -n "$sig" ] && [ "${sig%.*}" -le -75 ] 2>/dev/null; then warn="  <-- WEAK"; fi
        if [ -n "$inact" ] && [ "$inact" -ge 2000 ] 2>/dev/null; then warn="$warn  <-- NOT HEARD"; fi
        printf '%-8s %-18s %8s %10s%s\n' "$el" "$mac" "${sig:--}" "${inact:--}" "$warn"
      done

  sleep 1
done
echo "stopped. log: $LOG"
