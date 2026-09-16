#!/usr/bin/env bash
# Launch the detector inside the dusty-nv jetson-inference container.
#
# Modeled on jetson-inference's own docker/run.sh, with two deliberate changes:
#
#  * The image tag is PINNED to r36.3.0. Their tag.sh derives the tag from the
#    host's L4T version (r36.4.7 here), but dustynv has only published r36.2.0
#    and r36.3.0 — the auto-derived tag does not exist on Docker Hub.
#
#  * Instead of their camera device passthrough (--device /dev/video*), the
#    detector consumes the drone's MJPEG feed over HTTP (DET_STREAM_URL) via
#    --network host. No V4L2 device is mounted; nothing here touches a camera.
#
# What we keep is their cache layout: the cloned repo's data/ dir is mounted at
# /jetson-inference/data, so the on-demand model download (~68 MB for
# SSD-Mobilenet-v2) and the TensorRT engine build (minutes) land on the HOST and
# survive container exits. The container itself is disposable (--rm).
set -euo pipefail

IMAGE="${DET_IMAGE:-dustynv/jetson-inference:r36.3.0}"
NAME="${DET_CONTAINER:-drone-detector}"
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
JI_DIR="${JETSON_INFERENCE_DIR:-$(cd "$REPO_DIR/.." && pwd)/jetson-inference}"

if ! command -v docker >/dev/null 2>&1; then
  echo "docker not found on PATH" >&2
  exit 1
fi

if [ ! -f "$JI_DIR/data/networks/models.json" ]; then
  echo "jetson-inference clone not found at $JI_DIR (need its data/ dir for the model cache)" >&2
  echo "set JETSON_INFERENCE_DIR=/path/to/jetson-inference and re-run" >&2
  exit 1
fi

# The one-time TensorRT engine build needs several GB of RAM, and on Tegra the
# GPU allocates from the same physical RAM (NvMap, unswappable). With the desktop
# and a browser open an 8 GB board can fail the build with
# "NvMapMemAllocInternalTagged ... error 12" / "Could not initialize cudnn".
# Once the .engine file exists this doesn't matter — runtime needs far less.
# (-ipath: env names are lowercase 'ssd-mobilenet-v2', dirs are 'SSD-Mobilenet-v2')
NETWORK="${DET_NETWORK:-ssd-mobilenet-v2}"
avail_now() { awk '/MemAvailable/{print int($2/1024)}' /proc/meminfo; }

if [ -z "$(find "$JI_DIR/data/networks" -ipath "*${NETWORK}*" -name '*.engine' -print -quit 2>/dev/null)" ]; then
  if [ "$(avail_now)" -lt 4500 ]; then
    echo "WARNING: no cached TensorRT engine for $NETWORK yet and only $(avail_now) MB RAM available." >&2
    echo "         The first-run engine build may fail with an NvMap/cudnn OOM." >&2
    echo "         Close the browser (or: sudo systemctl isolate multi-user.target)" >&2
    echo "         for the one-time build, then re-run." >&2
  fi
fi

# Even with the engine cached, loading it still needs a few hundred MB of NvMap,
# and NvMap cannot use swap. Started at boot this races the desktop session, and
# losing that race is not graceful: TensorRT aborts the process *after* the HTTP
# thread has already bound :8091, so the port stays open with nothing behind it
# and the service looks alive while being dead.
#
# So wait for headroom instead of gambling. Bounded, because a ground station
# that never reaches the threshold should still try rather than hang forever.
DET_MIN_AVAIL_MB="${DET_MIN_AVAIL_MB:-1200}"
DET_MEM_WAIT_S="${DET_MEM_WAIT_S:-90}"
waited=0
while [ "$(avail_now)" -lt "$DET_MIN_AVAIL_MB" ] && [ "$waited" -lt "$DET_MEM_WAIT_S" ]; do
  [ "$waited" -eq 0 ] && echo "waiting for $DET_MIN_AVAIL_MB MB available (have $(avail_now) MB)..." >&2
  sleep 5
  waited=$((waited + 5))
done
if [ "$(avail_now)" -lt "$DET_MIN_AVAIL_MB" ]; then
  echo "proceeding after ${waited}s with only $(avail_now) MB available; load may fail" >&2
elif [ "$waited" -gt 0 ]; then
  echo "memory available after ${waited}s: $(avail_now) MB" >&2
fi

# Remove a leftover container from an earlier run of this script (the cache now
# lives on the host, so a stopped container holds nothing worth keeping).
if docker container inspect "$NAME" >/dev/null 2>&1; then
  if [ "$(docker container inspect -f '{{.State.Running}}' "$NAME")" = "true" ]; then
    echo "container '$NAME' is already running (docker stop $NAME first)" >&2
    exit 1
  fi
  docker rm "$NAME" >/dev/null
fi

# Allocate a TTY only when we actually have one. This script also runs headless
# (ssh without a pty, systemd), where docker refuses to start with -it:
# "cannot attach stdin to a TTY-enabled container because stdin is not a terminal".
TTY_FLAGS=()
[ -t 0 ] && TTY_FLAGS=(-it)

exec docker run --rm "${TTY_FLAGS[@]}" \
  --runtime nvidia \
  --network host \
  --name "$NAME" \
  -w /jetson-inference \
  -v "$JI_DIR/data:/jetson-inference/data" \
  -v "$REPO_DIR:/workspace:ro" \
  -e DET_STREAM_URL="${DET_STREAM_URL:-http://10.42.0.1:8090/stream.mjpg}" \
  -e DET_HOST="${DET_HOST:-0.0.0.0}" \
  -e DET_PORT="${DET_PORT:-8091}" \
  -e DET_NETWORK="$NETWORK" \
  -e DET_THRESHOLD="${DET_THRESHOLD:-0.5}" \
  -e PYTHONPATH=/workspace \
  "$IMAGE" \
  python3 -m detector "$@"
