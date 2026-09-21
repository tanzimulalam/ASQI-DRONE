#!/usr/bin/env bash
# Relax or restore the two arming relaxations needed to arm indoors.
#
#   benchmode.sh on       ARMING_CHECK=0 FENCE_ENABLE=0   props off, indoors only
#   benchmode.sh off      ARMING_CHECK=1 FENCE_ENABLE=1   required before flight
#   benchmode.sh status   read both off the vehicle, change nothing
#
# Run on the drone Jetson. The control daemon holds /dev/ttyACM0 exclusively, so
# this stops it, talks to the flight controller, and starts it again. The daemon
# is the safety boundary, so it is restarted on every exit path including failure.
#
# This exists because the two commands it replaces differed by a single character
# on a safety parameter, and that is a bad thing to get wrong at the end of a long
# bench session.

set -u

HERE="$(cd "$(dirname "$0")" && pwd)"
PARAMS="$HERE/params.py"
UNIT=drone-airborne
NAMES="ARMING_CHECK FENCE_ENABLE"

usage() {
    echo "usage: $(basename "$0") on|off|status" >&2
    exit 2
}

[ $# -eq 1 ] || usage
case "$1" in
    on)     want=0 ;;
    off)    want=1 ;;
    status) want="" ;;
    *)      usage ;;
esac

[ -f "$PARAMS" ] || { echo "cannot find $PARAMS" >&2; exit 1; }

# Use the interpreter the daemon itself runs under, because that is the one known
# to have pymavlink. On Piper that is the system python3; on Omega (JetPack 5) it
# is a venv, and the system python3 has no pymavlink at all. BENCHMODE_PYTHON
# overrides both.
PY="${BENCHMODE_PYTHON:-}"
if [ -z "$PY" ]; then
    PY="$(systemctl show -p ExecStart --value "$UNIT" 2>/dev/null \
        | sed -n 's/.*path=\([^ ;]*\).*/\1/p' | head -n1)"
    case "$PY" in
        *python*) ;;
        *) PY=python3 ;;
    esac
fi
"$PY" -c "import pymavlink" 2>/dev/null \
    || { echo "$PY has no pymavlink; set BENCHMODE_PYTHON to one that does" >&2; exit 1; }

restart_daemon() {
    sudo systemctl start "$UNIT" || true
    sleep 1
    echo "daemon: $(systemctl is-active "$UNIT")"
}

sudo systemctl stop "$UNIT"
trap restart_daemon EXIT

rc=0
if [ -z "$want" ]; then
    "$PY" "$PARAMS" get $NAMES || rc=$?
else
    set -- $NAMES
    assignments=""
    for n in "$@"; do assignments="$assignments $n=$want"; done
    # shellcheck disable=SC2086
    "$PY" "$PARAMS" set $assignments || rc=$?
    if [ "$rc" -eq 0 ]; then
        echo
        echo "read back from the vehicle:"
        "$PY" "$PARAMS" get $NAMES || rc=$?
    fi
fi

if [ "$rc" -ne 0 ]; then
    echo >&2
    echo "PARAMETER STEP FAILED." >&2
    echo "Whatever the vehicle printed above is what it actually holds." >&2
    echo "Do not fly until 'benchmode.sh off' reports both values as 1." >&2
elif [ "$want" = "0" ]; then
    cat <<'WARN'

  ==========================================================
   BENCH MODE ON

   Every pre-arm check is disabled and there is no geofence.
   Props stay off while this is set.

   Run 'benchmode.sh off' and confirm both values read 1
   before this aircraft goes outside.
  ==========================================================
WARN
fi

exit "$rc"
