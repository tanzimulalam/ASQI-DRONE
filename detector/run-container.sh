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
if [ -z "$(find "$JI_DIR/data/networks" -ipath "*${NETWORK}*" -name '*.engine' -print -quit 2>/dev/null)" ]; then
  avail_mb=$(awk '/MemAvailable/{print int($2/1024)}' /proc/meminfo)
  if [ "${avail_mb:-0}" -lt 4500 ]; then
    echo "WARNING: no cached TensorRT engine for $NETWORK yet and only ${avail_mb} MB RAM available." >&2
    echo "         The first-run engine build may fail with an NvMap/cudnn OOM." >&2
    echo "         Close the browser (or: sudo systemctl isolate multi-user.target)" >&2
    echo "         for the one-time build, then re-run." >&2
  fi
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

exec docker run --rm -it \
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
