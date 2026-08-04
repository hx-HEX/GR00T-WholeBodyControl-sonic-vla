# Low-latency SONIC teleoperation with Inspire hand

This note records the command sequence for running low-latency SONIC real-robot teleoperation with the Inspire/RH56 dexterous hands.

Assumptions:

- Robot IP: `192.168.123.164`
- Robot user: `unitree`
- Robot sudo password: `123`
- PC robot Ethernet interface: `enp6s0`
- PC workspace: `~/GR00T-WholeBodyControl`
- TensorRT path: `~/TensorRT`

## Terminal 1: robot, start Inspire hand SDK

```bash
ssh unitree@192.168.123.164
```

```bash
cd /home/unitree/develop/dfx_inspire_service/build

echo 123 | sudo -S pkill -f inspire_g1 || true

echo 123 | sudo -S nohup ./inspire_g1 > /tmp/inspire_g1.log 2>&1 &

tail -f /tmp/inspire_g1.log
```

## Terminal 2: PC, start low-latency SONIC deploy

```bash
cd ~/GR00T-WholeBodyControl/gear_sonic_deploy

export TensorRT_ROOT=$HOME/TensorRT

./deploy.sh \
  --cp policy/low_latency/model \
  --obs-config policy/low_latency/observation_config.yaml \
  --input-type zmq_manager \
  real
```

When prompted:

```bash
y
```

Expected deploy-side signs:

```text
ZMQManager Initialized
Realtime debug socket bound to port: 5557
Init Done
```

## Terminal 3: PC, start PICO manager

```bash
cd ~/GR00T-WholeBodyControl

source .venv_teleop/bin/activate

python gear_sonic/scripts/pico_manager_thread_server.py --manager
```

Expected PICO-side signs after connection stabilizes:

```text
Manager controls: A+X=toggle mode, A+B+X+Y=start/stop policy
[PicoReader] dt_ts: ...
[PoseLoop] FPS: ...
```

## Terminal 4: PC, start Inspire hand bridge

```bash
cd ~/GR00T-WholeBodyControl

tools/build/inspire_hand_zmq_bridge \
  --iface enp6s0 \
  --max-close 0.8
```

If left and right hands are reversed, use:

```bash
cd ~/GR00T-WholeBodyControl

tools/build/inspire_hand_zmq_bridge \
  --iface enp6s0 \
  --max-close 0.8 \
  --swap-hands
```

## PICO controls

```text
A+B+X+Y : start/stop policy
A+X     : switch between PLANNER and POSE
POSE    : full-body teleoperation
Left trigger / right trigger : close corresponding hand
```

Recommended operation:

1. Start all four terminals.
2. Keep the robot standing stably before starting the policy.
3. Press `A+B+X+Y` to start policy.
4. Use `A+X` to enter `POSE`.
5. Wait until `PoseLoop FPS` stabilizes near `50`.
6. Begin teleoperation.

