#!/usr/bin/env bash
# Install the drone-side services. Run on the DRONE Jetson:
#
#   sudo ./systemd/install.sh
#
# Idempotent: safe to re-run after pulling changes.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE=/etc/drone/drone.env

if [ "$(id -u)" -ne 0 ]; then
  echo "run me with sudo" >&2
  exit 1
fi

# --- shared secret -------------------------------------------------------- #
# Kept out of the repo and out of the unit file. Created with a placeholder on
# first install so the service has something to read; edit it before flying.
install -d -m 0755 /etc/drone
if [ ! -f "$ENV_FILE" ]; then
  cat > "$ENV_FILE" <<'EOF'
# Shared secret between the drone and the ground station. The operator types
# this at the GUI login. It must match GROUND_* usage on the ground Jetson.
DRONE_SESSION_TOKEN=change-me

# Flight controller. Override if the Pixhawk enumerates elsewhere.
DRONE_MAV_DEVICE=/dev/ttyACM0
EOF
  chmod 0640 "$ENV_FILE"
  echo "created $ENV_FILE — EDIT THE TOKEN before flying"
else
  echo "$ENV_FILE already exists, leaving it alone"
fi

# --- units ---------------------------------------------------------------- #
install -m 0644 "$HERE/drone-airborne.service" /etc/systemd/system/
install -m 0644 "$HERE/drone-camera.service"   /etc/systemd/system/

# --- radio settings on hotspot up ----------------------------------------- #
install -d -m 0755 /etc/NetworkManager/dispatcher.d
install -m 0755 "$HERE/90-drone-hotspot" /etc/NetworkManager/dispatcher.d/

# --- hotspot starts itself ------------------------------------------------ #
if nmcli -t -f NAME con show | grep -qx drone-hotspot; then
  nmcli con mod drone-hotspot connection.autoconnect yes connection.autoconnect-priority 10
  echo "drone-hotspot set to autoconnect"
else
  echo "WARNING: no 'drone-hotspot' connection found; create it before flying" >&2
fi

systemctl daemon-reload
systemctl enable --now drone-airborne.service drone-camera.service

echo
systemctl --no-pager --lines=0 status drone-airborne.service drone-camera.service || true
echo
echo "done. check: journalctl -u drone-airborne -f"
