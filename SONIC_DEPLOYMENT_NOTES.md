# SONIC deployment notes for this workspace

This branch contains the local deployment additions used on the G1 + RealSense + Inspire/RH56 hand setup.

It is intended as an engineering handoff for reproducing the current workstation/robot workflow. It does not replace the upstream documentation.

## Current machine assumptions

- Workstation repo path:

  ```bash
  ~/GR00T-WholeBodyControl
  ```

- Robot wired IP used during testing:

  ```text
  192.168.123.164
  ```

- Workstation wired robot interface during testing:

  ```text
  enp6s0
  ```

- PICO should connect to the workstation WiFi IP, not the robot IP. Check with:

  ```bash
  ip -br addr
  ```

## Files added in this branch

### RealSense camera server

```text
tools/realsense_color_zmq_server_py38.py
```

Purpose:

- Runs on the robot with system Python 3.8.
- Reads the built-in RealSense D435i color stream via `pyrealsense2`.
- Publishes a GR00T-compatible ZMQ camera stream on port `5555`.
- Publishes the image key expected by the VLA/data collection stack:

  ```text
  ego_view
  ```

This is used because the official camera server path is primarily tested with OAK cameras. The repository has RealSense drivers, but on this robot the Python 3.10 `pyrealsense2` wheel required a newer GLIBC than the robot OS provides. The Python 3.8 path works with the installed RealSense wheel.

### Inspire/RH56 hand bridge from SONIC

```text
tools/inspire_hand_zmq_bridge.cpp
tools/build/inspire_hand_zmq_bridge
```

Purpose:

- Runs on the workstation.
- Subscribes to SONIC deploy debug state:

  ```text
  tcp://localhost:5557, topic g1_debug
  ```

- Reads:

  ```text
  last_left_hand_action
  last_right_hand_action
  ```

- Converts SONIC/Dex3-style 7D hand targets to Inspire/RH56 6D normalized positions.
- Publishes DDS commands to:

  ```text
  rt/inspire/cmd
  ```

The bridge is intentionally separate from the main SONIC deploy process. If it is not running, SONIC still controls the body normally and does not drive the Inspire hands.

### Inspire/RH56 manual hand control

```text
tools/inspire_hand_manual_control.cpp
tools/build/inspire_hand_manual_control
```

Purpose:

- Runs on the workstation.
- Directly publishes Inspire/RH56 hand commands to `rt/inspire/cmd`.
- Does not require PICO.
- Useful for checking hand wiring, left/right mapping, open/close direction, and individual finger order.

Inspire/RH56 command convention:

```text
q = 1.0  open
q = 0.0  close
```

Per-hand joint order:

```text
pinky, ring, middle, index, thumb_bend, thumb_rotation
```

### Low-latency SONIC deployment policy

```text
gear_sonic_deploy/policy/low_latency/model_decoder.onnx
gear_sonic_deploy/policy/low_latency/model_encoder.onnx
gear_sonic_deploy/policy/low_latency/observation_config.yaml
```

Purpose:

- Uses the low-latency SONIC checkpoint.
- Uses 4 future frames at 20 ms spacing, approximately 80 ms reference lookahead.
- Must use encoder, decoder, and observation config together.

Notes:

- `.onnx` files are tracked through Git LFS.
- `.trt` files are local TensorRT engine cache files and are ignored; they are regenerated automatically on the target GPU.

## One-time robot-side services

### Built-in RealSense camera

First make sure the RealSense enumerates:

```bash
ssh unitree@192.168.123.164

lsusb | grep -Ei '8086|intel|realsense'
ls -l /dev/video*
```

Expected:

```text
ID 8086:0b3a Intel Corp.
```

If RealSense does not appear, reboot the robot/onboard computer. USB controller resets can leave the xHCI controller wedged.

Start the RealSense ZMQ camera server on the robot:

```bash
ssh unitree@192.168.123.164

/unitree/sbin/mscli stopservice video_hub_pc4 2>/dev/null || true

cd ~/groot_camera_server
/usr/bin/python3 realsense_color_zmq_server_py38.py \
  --port 5555 \
  --width 640 \
  --height 480 \
  --fps 30
```

Verify from the workstation:

```bash
cd ~/GR00T-WholeBodyControl
source .venv_data_collection/bin/activate

python gear_sonic/scripts/run_camera_viewer.py \
  --camera-host 192.168.123.164 \
  --camera-port 5555
```

If OpenCV GUI is unavailable in the venv, use the camera client or record one frame instead of `imshow`.

### Inspire/RH56 hand serial service

The robot-side service is:

```text
/home/unitree/develop/dfx_inspire_service/build/inspire_g1
```

It bridges:

```text
DDS rt/inspire/cmd/state <-> /dev/ttyUSB1 and /dev/ttyUSB2 <-> Inspire hands
```

Start it on the robot:

```bash
ssh unitree@192.168.123.164

cd /home/unitree/develop/dfx_inspire_service/build
echo 123 | sudo -S nohup ./inspire_g1 > /tmp/inspire_g1.log 2>&1 &
```

Check:

```bash
ps -ef | grep inspire_g1 | grep -v grep
tail -f /tmp/inspire_g1.log
```

## Sim2Sim startup

Use three terminals.

### Terminal 1: MuJoCo simulator

From the repo root:

```bash
cd ~/GR00T-WholeBodyControl
source .venv_teleop/bin/activate

python gear_sonic/scripts/run_sim_loop.py
```

For data collection or VLA camera testing in simulation, publish camera images:

```bash
cd ~/GR00T-WholeBodyControl
source .venv_sim/bin/activate

python gear_sonic/scripts/run_sim_loop.py \
  --enable-image-publish \
  --enable-offscreen \
  --camera-port 5555
```

### Terminal 2: default SONIC deploy in simulation

```bash
cd ~/GR00T-WholeBodyControl/gear_sonic_deploy
export TensorRT_ROOT=$HOME/TensorRT

./deploy.sh \
  --input-type zmq_manager \
  sim
```

Wait for:

```text
Init Done
```

### Terminal 2 alternative: low-latency SONIC deploy in simulation

```bash
cd ~/GR00T-WholeBodyControl/gear_sonic_deploy
export TensorRT_ROOT=$HOME/TensorRT

./deploy.sh \
  --cp policy/low_latency/model \
  --obs-config policy/low_latency/observation_config.yaml \
  --input-type zmq_manager \
  sim
```

### Terminal 3: PICO manager

```bash
cd ~/GR00T-WholeBodyControl
source .venv_teleop/bin/activate

python gear_sonic/scripts/pico_manager_thread_server.py --manager
```

Operator sequence:

```text
A+X+B+Y  initialize/calibrate
A+X      enter POSE whole-body teleop
```

## Real robot startup

Before running real control:

- Robot is in debug mode.
- Robot is safely supported or in a controlled test setup.
- Workstation wired interface is on the Unitree network, e.g. `192.168.123.x`.
- RealSense camera server is running if collecting data or running VLA.
- Inspire hand service is running if controlling Inspire hands.

### Terminal 1: default SONIC deploy on real robot

```bash
cd ~/GR00T-WholeBodyControl/gear_sonic_deploy
export TensorRT_ROOT=$HOME/TensorRT

./deploy.sh \
  --input-type zmq_manager \
  real
```

### Terminal 1 alternative: low-latency SONIC deploy on real robot

```bash
cd ~/GR00T-WholeBodyControl/gear_sonic_deploy
export TensorRT_ROOT=$HOME/TensorRT

./deploy.sh \
  --cp policy/low_latency/model \
  --obs-config policy/low_latency/observation_config.yaml \
  --input-type zmq_manager \
  real
```

Wait for:

```text
Init Done
```

### Terminal 2: PICO manager

```bash
cd ~/GR00T-WholeBodyControl
source .venv_teleop/bin/activate

python gear_sonic/scripts/pico_manager_thread_server.py --manager
```

If it prints `waiting for body data...`, the PICO is connected but full body data is not reaching XRoboToolkit SDK. Check:

```bash
cd ~/GR00T-WholeBodyControl
source .venv_teleop/bin/activate

python -c 'import time,xrobotoolkit_sdk as xrt; xrt.init();
while True:
    print("body", xrt.is_body_data_available(), "ts", xrt.get_time_stamp_ns(), "body_ts", xrt.get_body_timestamp_ns(), "trackers", xrt.get_motion_tracker_serial_numbers(), "LT/RT", xrt.get_left_trigger(), xrt.get_right_trigger(), "LG/RG", xrt.get_left_grip(), xrt.get_right_grip(), "axes", xrt.get_left_axis(), xrt.get_right_axis())
    time.sleep(0.5)'
```

Target state:

```text
body True
body_ts nonzero
```

## Inspire/RH56 hand testing without PICO

These commands do not require SONIC deploy or PICO. The robot-side `inspire_g1` service must be running.

Open both hands:

```bash
cd ~/GR00T-WholeBodyControl

tools/build/inspire_hand_manual_control \
  --iface enp6s0 \
  --hand both \
  --pose open \
  --duration 3
```

Small close test:

```bash
cd ~/GR00T-WholeBodyControl

tools/build/inspire_hand_manual_control \
  --iface enp6s0 \
  --hand both \
  --pose close \
  --max-close 0.2 \
  --duration 3
```

Right index finger only:

```bash
cd ~/GR00T-WholeBodyControl

tools/build/inspire_hand_manual_control \
  --iface enp6s0 \
  --hand right \
  --pose custom \
  --right 1,1,1,0.7,1,1 \
  --duration 2
```

Left index finger only:

```bash
cd ~/GR00T-WholeBodyControl

tools/build/inspire_hand_manual_control \
  --iface enp6s0 \
  --hand left \
  --pose custom \
  --left 1,1,1,0.7,1,1 \
  --duration 2
```

## Inspire/RH56 hand following SONIC/PICO

Start the normal SONIC deploy and PICO manager first. Then run the bridge on the workstation.

Dry-run first; this only prints converted hand commands and does not publish DDS:

```bash
cd ~/GR00T-WholeBodyControl

tools/build/inspire_hand_zmq_bridge \
  --iface enp6s0 \
  --max-close 0.3 \
  --dry-run
```

If `R [...]` and `L [...]` change when PICO triggers/grips move, run the real bridge:

```bash
cd ~/GR00T-WholeBodyControl

tools/build/inspire_hand_zmq_bridge \
  --iface enp6s0 \
  --max-close 0.2
```

Increase closure only after direction and safety are confirmed:

```bash
--max-close 0.3
--max-close 0.5
--max-close 0.8
```

If left/right hands are reversed:

```bash
tools/build/inspire_hand_zmq_bridge \
  --iface enp6s0 \
  --max-close 0.2 \
  --swap-hands
```

## Data collection with low-latency SONIC

Camera server must be running on the robot.

```bash
cd ~/GR00T-WholeBodyControl
export TensorRT_ROOT=$HOME/TensorRT

python gear_sonic/scripts/launch_data_collection.py \
  --camera-host 192.168.123.164 \
  --camera-port 5555 \
  --task-prompt "pick up the box and place it on the table" \
  --dataset-name g1_box_pick_place_low_latency \
  --deploy-checkpoint policy/low_latency/model \
  --deploy-obs-config policy/low_latency/observation_config.yaml
```

PICO recording controls:

```text
A+X+B+Y       initialize/calibrate
A+X           enter POSE
Left Grip + A start/stop recording
Left Grip + B discard current episode
```

## VLA notes

The GR00T VLA client needs:

- PolicyServer running on the workstation.
- SONIC deploy publishing `g1_debug` on `localhost:5557`.
- Camera server publishing `ego_view` on the robot.
- `run_vla_inference.py` connected to the PolicyServer and camera server.

Example client:

```bash
cd ~/GR00T-WholeBodyControl
source .venv_inference/bin/activate

python gear_sonic/scripts/run_vla_inference.py \
  --host localhost \
  --port 5550 \
  --embodiment-tag unitree_g1_sonic \
  --prompt "reach the bottle with the right hand" \
  --camera-host 192.168.123.164 \
  --camera-port 5555
```

If it prints `waiting for state msg`, SONIC deploy is not publishing usable `g1_debug` state, or the VLA client cannot connect to `localhost:5557`.

