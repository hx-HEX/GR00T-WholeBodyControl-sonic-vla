#!/usr/bin/env bash
set -euo pipefail

# Start the robot-mounted RealSense camera server with timestamps corrected to
# the PC clock. This avoids corrupting VLA/data-collection alignment when the
# robot's system clock drifts from the workstation clock.
#
# Environment overrides:
#   ROBOT_HOST=192.168.123.164
#   ROBOT_USER=unitree
#   ROBOT_PASS=123
#   ROBOT_REPO=~/GR00T-WholeBodyControl
#   CAMERA_PORT=5555
#   CAMERA_WIDTH=640
#   CAMERA_HEIGHT=480
#   CAMERA_FPS=30
#   OFFSET_SAMPLES=8
#   STOP_FACTORY_REALSENSE_HUB=1
#   START_INSPIRE_HAND_SDK=1
#   INSPIRE_SDK_DIR=/home/unitree/develop/dfx_inspire_service/build
#   INSPIRE_SDK_BIN=inspire_g1

ROBOT_HOST="${ROBOT_HOST:-192.168.123.164}"
ROBOT_USER="${ROBOT_USER:-unitree}"
ROBOT_PASS="${ROBOT_PASS:-123}"
ROBOT_REPO="${ROBOT_REPO:-~/GR00T-WholeBodyControl}"
CAMERA_PORT="${CAMERA_PORT:-5555}"
CAMERA_WIDTH="${CAMERA_WIDTH:-640}"
CAMERA_HEIGHT="${CAMERA_HEIGHT:-480}"
CAMERA_FPS="${CAMERA_FPS:-30}"
OFFSET_SAMPLES="${OFFSET_SAMPLES:-8}"
STOP_FACTORY_REALSENSE_HUB="${STOP_FACTORY_REALSENSE_HUB:-1}"
START_INSPIRE_HAND_SDK="${START_INSPIRE_HAND_SDK:-1}"
INSPIRE_SDK_DIR="${INSPIRE_SDK_DIR:-/home/unitree/develop/dfx_inspire_service/build}"
INSPIRE_SDK_BIN="${INSPIRE_SDK_BIN:-inspire_g1}"

SSH=(sshpass -p "$ROBOT_PASS" ssh -o StrictHostKeyChecking=no "${ROBOT_USER}@${ROBOT_HOST}")
SCP=(sshpass -p "$ROBOT_PASS" scp -o StrictHostKeyChecking=no)

echo "[camera] syncing server script to robot..."
"${SSH[@]}" "mkdir -p ${ROBOT_REPO}/tools"
"${SCP[@]}" tools/realsense_color_zmq_server_py38.py \
  "${ROBOT_USER}@${ROBOT_HOST}:${ROBOT_REPO}/tools/realsense_color_zmq_server_py38.py" >/dev/null

echo "[camera] estimating PC - robot clock offset..."
OFFSET="$(
  OFFSET_SAMPLES="$OFFSET_SAMPLES" ROBOT_HOST="$ROBOT_HOST" ROBOT_USER="$ROBOT_USER" ROBOT_PASS="$ROBOT_PASS" \
  python - <<'PY'
import os
import subprocess
import time

samples = []
count = int(os.environ["OFFSET_SAMPLES"])
host = os.environ["ROBOT_HOST"]
user = os.environ["ROBOT_USER"]
password = os.environ["ROBOT_PASS"]

for _ in range(count):
    t0 = time.time()
    robot_time = subprocess.check_output(
        [
            "sshpass",
            "-p",
            password,
            "ssh",
            "-o",
            "StrictHostKeyChecking=no",
            f"{user}@{host}",
            "date +%s.%N",
        ],
        text=True,
    ).strip()
    t1 = time.time()
    rtt = t1 - t0
    offset = (t0 + t1) / 2.0 - float(robot_time)
    samples.append((rtt, offset))

best_rtt, best_offset = min(samples, key=lambda x: x[0])
print(f"{best_offset:.9f}")
print(f"[camera] best_rtt_ms={best_rtt*1000:.2f} offset_sec={best_offset:.9f}", file=__import__("sys").stderr)
PY
)"

echo "[camera] restarting robot camera server on port ${CAMERA_PORT} with offset ${OFFSET}s..."
"${SSH[@]}" "
if [ '${START_INSPIRE_HAND_SDK}' = '1' ]; then
  echo '[hand] restarting Inspire hand SDK ${INSPIRE_SDK_BIN}...'
  if [ -x '${INSPIRE_SDK_DIR}/${INSPIRE_SDK_BIN}' ]; then
    echo '${ROBOT_PASS}' | sudo -S pkill -x '${INSPIRE_SDK_BIN}' || true
    sleep 1
    echo '${ROBOT_PASS}' | sudo -S sh -c \"cd '${INSPIRE_SDK_DIR}' && nohup ./'${INSPIRE_SDK_BIN}' > /tmp/inspire_g1.log 2>&1 &\"
    sleep 1
    if pgrep -x '${INSPIRE_SDK_BIN}' >/dev/null; then
      echo '[hand] Inspire hand SDK started.'
      tail -n 20 /tmp/inspire_g1.log || true
    else
      echo '[hand] ERROR: Inspire hand SDK did not stay running. Log:'
      tail -n 80 /tmp/inspire_g1.log || true
      exit 1
    fi
  else
    echo '[hand] ERROR: Inspire SDK binary not found or not executable: ${INSPIRE_SDK_DIR}/${INSPIRE_SDK_BIN}'
    exit 1
  fi
fi

if [ '${STOP_FACTORY_REALSENSE_HUB}' = '1' ]; then
  echo '[camera] stopping factory RealSense video hub service video_hub_pc4...'
  if [ -x /unitree/sbin/mscli ]; then
    echo '${ROBOT_PASS}' | sudo -S /unitree/sbin/mscli stopservice video_hub_pc4 || true
  fi
  # Fallback in case master_service has already spawned it or mscli is absent.
  factory_pids=\$(pgrep -f '^/unitree/module/video_hub_pc4/videohub_pc4 /dev/video4' || true)
  if [ -n \"\$factory_pids\" ]; then
    echo '${ROBOT_PASS}' | sudo -S kill \$factory_pids || true
    sleep 1
  fi
fi

port_pids=\$(ss -ltnp 2>/dev/null | awk '/:${CAMERA_PORT}/ { if (match(\$0,/pid=[0-9]+/)) { print substr(\$0,RSTART+4,RLENGTH-4) } }' | sort -u)
if [ -n \"\$port_pids\" ]; then kill \$port_pids || true; sleep 1; fi
port_pids=\$(ss -ltnp 2>/dev/null | awk '/:${CAMERA_PORT}/ { if (match(\$0,/pid=[0-9]+/)) { print substr(\$0,RSTART+4,RLENGTH-4) } }' | sort -u)
if [ -n \"\$port_pids\" ]; then kill -9 \$port_pids || true; sleep 1; fi

cd ${ROBOT_REPO} || exit 1
nohup /usr/bin/python3 -u tools/realsense_color_zmq_server_py38.py \
  --port ${CAMERA_PORT} \
  --width ${CAMERA_WIDTH} \
  --height ${CAMERA_HEIGHT} \
  --fps ${CAMERA_FPS} \
  --timestamp-offset-sec ${OFFSET} \
  > /tmp/realsense_color_zmq_server.log 2>&1 &
echo camera_pid=\$!
sleep 3
if ss -ltnp | grep :${CAMERA_PORT}; then
  tail -n 40 /tmp/realsense_color_zmq_server.log
  exit 0
fi

echo '[camera] server did not bind; checking log...'
tail -n 80 /tmp/realsense_color_zmq_server.log || true

if grep -q 'Device or resource busy\\|VIDIOC_S_FMT' /tmp/realsense_color_zmq_server.log 2>/dev/null; then
  echo '[camera] RealSense is busy; sending hardware reset and retrying...'
  /usr/bin/python3 - <<'PY'
import time
import pyrealsense2 as rs

ctx = rs.context()
devs = list(ctx.query_devices())
print('devices', len(devs))
for dev in devs:
    print(dev.get_info(rs.camera_info.name), dev.get_info(rs.camera_info.serial_number))
    dev.hardware_reset()
    print('hardware_reset sent')
time.sleep(6)
PY

  nohup /usr/bin/python3 -u tools/realsense_color_zmq_server_py38.py \
    --port ${CAMERA_PORT} \
    --width ${CAMERA_WIDTH} \
    --height ${CAMERA_HEIGHT} \
    --fps ${CAMERA_FPS} \
    --timestamp-offset-sec ${OFFSET} \
    > /tmp/realsense_color_zmq_server.log 2>&1 &
  echo camera_pid=\$!
  sleep 3
fi

ss -ltnp | grep :${CAMERA_PORT}
tail -n 40 /tmp/realsense_color_zmq_server.log
"

echo "[camera] started. Verify from PC with:"
echo "  cd ~/GR00T-WholeBodyControl && source .venv_data_collection/bin/activate && python gear_sonic/scripts/run_camera_viewer.py --camera-host ${ROBOT_HOST} --camera-port ${CAMERA_PORT}"
