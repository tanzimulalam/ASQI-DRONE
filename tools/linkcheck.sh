#!/usr/bin/env bash
# Live wireless link monitor for the range walk-test. Run on the GROUND Jetson
# while someone carries the drone away from the station.
#
#   ./tools/linkcheck.sh                 # defaults below
#   IFACE=wlP1p1s0 PEER=10.42.0.1 ./tools/linkcheck.sh
#
# One line per second:
#   signal  the RSSI the ground station hears from the drone's AP
#   tx/rx   the negotiated 802.11 rates (these drop before the link actually
#           fails, so they are the early warning)
#   rtt     round trip to the drone, and whether the ping was lost at all
#   video   bytes pulled from the MJPEG stream in the last second
#
# Rough RSSI guide on 2.4 GHz 802.11n:
#   -30..-55  excellent      -56..-67  good, full rate
#   -68..-74  usable, rate will fall back
#   -75..-82  marginal, expect video stutter and control gaps
#   below -82 do not fly here
set -uo pipefail

IFACE="${IFACE:-wlP1p1s0}"
PEER="${PEER:-10.42.0.1}"
CAM_PORT="${CAM_PORT:-8090}"
LOG="${LOG:-/tmp/linkcheck-$(date +%H%M%S).csv}"

if ! iw dev "$IFACE" link >/dev/null 2>&1; then
  echo "no such wireless interface: $IFACE" >&2
  exit 1
fi

echo "logging to $LOG   (peer $PEER, iface $IFACE)"
echo "time,signal_dbm,tx_mbps,rx_mbps,rtt_ms,loss,video_kbps" > "$LOG"
printf '%-9s %8s %9s %9s %9s %7s  %s\n' TIME SIGNAL TX RX RTT LOSS VIDEO

while true; do
  link=$(iw dev "$IFACE" link 2>/dev/null)
  sig=$(awk '/signal:/{print $2}' <<<"$link")
  tx=$(awk '/tx bitrate:/{print $3}' <<<"$link")
  rx=$(awk '/rx bitrate:/{print $3}' <<<"$link")

  # One ping, 1 s deadline, so the loop stays on cadence when the link is down.
  if p=$(ping -c 1 -W 1 -I "$IFACE" "$PEER" 2>/dev/null); then
    rtt=$(sed -n 's/.*time=\([0-9.]*\).*/\1/p' <<<"$p")
    loss=ok
  else
    rtt=""
    loss=LOST
  fi

  # 1 s of the live stream tells us whether video is actually flowing, which the
  # 802.11 rate alone will not: the radio can be associated and still starving.
  vbytes=$(timeout 1.2 curl -s --interface "$IFACE" --max-time 1 \
             "http://${PEER}:${CAM_PORT}/stream.mjpg" 2>/dev/null | wc -c)
  vkbps=$(( vbytes * 8 / 1000 ))

  ts=$(date +%H:%M:%S)
  # Flag the operator when the link enters the range where it starts to hurt.
  warn=""
  if [ -n "${sig:-}" ] && [ "${sig%.*}" -le -75 ] 2>/dev/null; then warn="  <-- WEAK"; fi
  if [ "$loss" = LOST ]; then warn="  <-- PACKET LOST"; fi

  printf '%-9s %8s %9s %9s %9s %7s  %6s kbps%s\n' \
    "$ts" "${sig:--}" "${tx:--}" "${rx:--}" "${rtt:--}" "$loss" "$vkbps" "$warn"
  echo "$ts,${sig:-},${tx:-},${rx:-},${rtt:-},$loss,$vkbps" >> "$LOG"
done
