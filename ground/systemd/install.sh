#!/usr/bin/env bash
# Install the ground-side services. Run on the GROUND Jetson:
#
#   sudo ./systemd/install.sh
#
# Idempotent: safe to re-run after pulling changes.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE=/etc/drone/ground.env

if [ "$(id -u)" -ne 0 ]; then
  echo "run me with sudo" >&2
  exit 1
fi

install -d -m 0755 /etc/drone
if [ ! -f "$ENV_FILE" ]; then
  cat > "$ENV_FILE" <<'EOF'
# Candidate aircraft, tried in order at login. The hotspot address comes first
# so a field setup works with no edits; the wired address is the bench fallback.
GROUND_AIRBORNE_IPS=10.42.0.1,10.131.70.137

# Where the detector pulls frames from. The drone's own AP address, so this is
# correct in flight; point it at the wired IP for bench work without a hotspot.
DET_STREAM_URL=http://10.42.0.1:8090/stream.mjpg
DET_NETWORK=ssd-mobilenet-v2
DET_THRESHOLD=0.5
EOF
  chmod 0644 "$ENV_FILE"
  echo "created $ENV_FILE"
else
  echo "$ENV_FILE already exists, leaving it alone"
fi

install -m 0644 "$HERE/ground-bridge.service"   /etc/systemd/system/
install -m 0644 "$HERE/ground-detector.service" /etc/systemd/system/

# Restores regdomain and TX power whenever the station joins the drone's hotspot.
# Without it this end sits on the world-domain fallback at 15 dBm, on the weaker
# half of an already asymmetric link.
install -d -m 0755 /etc/NetworkManager/dispatcher.d
install -m 0755 "$HERE/90-drone-link" /etc/NetworkManager/dispatcher.d/

# The ground station joins the drone's hotspot on its own. Route metric keeps
# any wired network as the default route, so this never steals ssh.
if nmcli -t -f NAME con show | grep -qx drone-link; then
  nmcli con mod drone-link connection.autoconnect yes ipv4.route-metric 700
  echo "drone-link set to autoconnect"
else
  echo "note: no 'drone-link' connection yet; create it to auto-join the drone AP"
fi

systemctl daemon-reload
systemctl enable --now ground-bridge.service ground-detector.service

echo
systemctl --no-pager --lines=0 status ground-bridge.service ground-detector.service || true
echo
echo "done. GUI: http://\$(hostname -I | awk '{print \$1}'):8000/"
echo "logs:  journalctl -u ground-bridge -f   /   journalctl -u ground-detector -f"
