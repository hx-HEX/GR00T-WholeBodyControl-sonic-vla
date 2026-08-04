# VLA 实机验证部署流程

这份流程用于当前本机环境的 VLA 实机验证：

- PC workspace: `~/GR00T-WholeBodyControl`
- Isaac-GR00T repo: `~/GR00T/repos/Isaac-GR00T`
- Robot IP: `192.168.123.164`
- Robot user: `unitree`
- Robot sudo password: `123`
- Robot camera: RealSense D435I
- Hand: Inspire/RH56
- SONIC deploy: low-latency checkpoint
- VLA checkpoint: `sonic_vla_bottle_to_bin`

重要：low-latency SONIC 不要按 `i`。`i` 会发送 VLA client 内置的 `LATENT_INITIAL_MOTION_TOKEN`，这个 token 不一定匹配当前 low-latency SONIC checkpoint，可能导致姿态异常。当前流程使用：

```text
k → o → p
```

其中 `o` 是我们新增的安全切换键：只切到 POSE，不发送 initial token。

## 0. 可选：清理旧进程

PC 上：

```bash
pkill -f run_vla_inference.py || true
pkill -f run_gr00t_server.py || true
pkill -f inspire_hand_zmq_bridge || true
pkill -f pico_manager_thread_server.py || true
pkill -f g1_deploy_onnx_ref || true
```

机器人相机/手 SDK 如果需要重启，后面脚本会处理。

## 1. 机器人：启动 RealSense 相机和 Inspire 手 SDK

在 PC 上执行：

```bash
cd ~/GR00T-WholeBodyControl

./tools/start_robot_realsense_camera.sh
```

预期：

```text
[camera] restarting robot camera server on port 5555
[hand] restarting Inspire hand SDK inspire_g1
```

如果脚本因为 sudo 密码卡住，可以手动 SSH 到机器人：

```bash
ssh unitree@192.168.123.164
```

机器人上启动相机：

```bash
cd ~/GR00T-WholeBodyControl

pkill -f realsense_color_zmq_server_py38.py || true
pkill -f composed_camera || true

/usr/bin/python3 tools/realsense_color_zmq_server_py38.py \
  --port 5555 \
  --width 640 \
  --height 480 \
  --fps 30
```

另一个机器人终端启动手 SDK：

```bash
cd /home/unitree/develop/dfx_inspire_service/build

echo 123 | sudo -S pkill -f inspire_g1 || true

echo 123 | sudo -S nohup ./inspire_g1 > /tmp/inspire_g1.log 2>&1 &

tail -f /tmp/inspire_g1.log
```

## 2. PC：启动 low-latency SONIC deploy

```bash
cd ~/GR00T-WholeBodyControl/gear_sonic_deploy

export TensorRT_ROOT=$HOME/TensorRT

./deploy.sh \
  --cp policy/low_latency/model \
  --obs-config policy/low_latency/observation_config.yaml \
  --input-type zmq_manager \
  real
```

提示时输入：

```text
y
```

等到看到：

```text
Init Done
```

## 3. PC：启动 VLA PolicyServer

### 10k checkpoint

```bash
cd ~/GR00T/repos/Isaac-GR00T

~/GR00T-WholeBodyControl/.venv_inference/bin/python \
  gr00t/eval/run_gr00t_server.py \
  --model-path ~/GR00T/models/huggingface_and_checkpoints/sonic_vla_bottle_to_bin/10k-checkpoint-10000 \
  --embodiment-tag UNITREE_G1_SONIC \
  --device cuda:0 \
  --host 0.0.0.0 \
  --port 5550
```

### 20k checkpoint

如果要测试 20k，先停掉旧 PolicyServer：

```bash
pkill -f run_gr00t_server.py || true
```

然后启动：

```bash
cd ~/GR00T/repos/Isaac-GR00T

~/GR00T-WholeBodyControl/.venv_inference/bin/python \
  gr00t/eval/run_gr00t_server.py \
  --model-path ~/GR00T/models/huggingface_and_checkpoints/sonic_vla_bottle_to_bin/20k-checkpoint-20000 \
  --embodiment-tag UNITREE_G1_SONIC \
  --device cuda:0 \
  --host 0.0.0.0 \
  --port 5550
```

预期：

```text
Server is ready and listening on tcp://0.0.0.0:5550
```

## 4. PC：启动 VLA inference client

需要重启此进程才能使用新增的 `o` 键。

```bash
cd ~/GR00T-WholeBodyControl

source .venv_inference/bin/activate

python gear_sonic/scripts/run_vla_inference.py \
  --host localhost \
  --port 5550 \
  --embodiment-tag unitree_g1_sonic \
  --prompt "pick up the bottle and put it into the trash bin" \
  --camera-host 192.168.123.164 \
  --camera-port 5555 \
  --sync-state-to-image \
  --state-sync-buffer-size 300 \
  --state-sync-max-age-ms 250
```

如果看到：

```text
waiting for state msg
```

说明 SONIC deploy 还没有进入 control 或还没发布 `g1_debug`，不一定是错误。

## 5. PC：启动 keyboard publisher

复制这一整行：

```bash
cd ~/GR00T-WholeBodyControl && source .venv_inference/bin/activate && python -c 'import zmq,time; ctx=zmq.Context(); pub=ctx.socket(zmq.PUB); pub.bind("tcp://localhost:5580"); time.sleep(0.5); print("ready: input k/o/p or t <new prompt>"); exec("while True:\n    s=input()\n    pub.send_string(s)\n    print(\"Sent:\", s)")'
```

后续所有 VLA 控制按键都在这个终端输入。

## 6. PC：启动 Inspire 手桥

```bash
cd ~/GR00T-WholeBodyControl

tools/build/inspire_hand_zmq_bridge \
  --iface enp6s0 \
  --max-close 0.8
```

如果左右手反了：

```bash
cd ~/GR00T-WholeBodyControl

tools/build/inspire_hand_zmq_bridge \
  --iface enp6s0 \
  --max-close 0.8 \
  --swap-hands
```

## 7. VLA 操作顺序

不要按 `i`。

在 keyboard publisher 终端依次输入：

```text
k
```

等待 SONIC 进入 PLANNER，然后输入：

```text
o
```

这一步只切到 POSE，不发送 initial token。

确认机器人仍稳定后，输入：

```text
p
```

此时 VLA client 会开始持续发送推理动作。

推荐首次验证节奏：

```text
k
o
p
运行 1~2 秒
p
k
```

含义：

| 输入 | 作用 |
|---|---|
| `k` | 启动/停止 SONIC C++ control loop |
| `o` | 从 PLANNER 切到 POSE，不发送 initial token |
| `p` | pause/resume VLA policy loop |
| `t <prompt>` | 运行时切换语言命令 |
| `i` | 发送内置 initial token；low-latency 版本不要用 |

## 8. 切换语言命令

在 keyboard publisher 终端输入：

```text
t pick up the bottle and put it into the trash bin
```

或者更保守一点：

```text
t reach the bottle with the right hand
```

注意：如果模型/数据主要是“桌上拿水瓶并丢进垃圾桶”，prompt 应尽量接近训练数据的语言分布。

## 9. 可视化相机

可选，用来确认 RealSense 是否看到物体：

```bash
cd ~/GR00T-WholeBodyControl

source .venv_data_collection/bin/activate

python gear_sonic/scripts/run_camera_viewer.py \
  --camera-host 192.168.123.164 \
  --camera-port 5555
```

如果出现 OpenCV `imshow` 不支持，说明当前环境缺 GUI highgui，数据采集/推理本身不一定受影响。

## 10. 常见问题

### 机器人不动

检查：

- PolicyServer 是否显示 ready
- VLA client 是否连接到 PolicyServer
- SONIC deploy 是否 `Init Done`
- 是否按了 `k → o → p`
- VLA client 是否打印：

```text
New action chunk
ZMQ: Sent latent action
```

### 不要用 `i`

`i` 会发：

```text
LATENT_INITIAL_MOTION_TOKEN
```

当前 low-latency SONIC checkpoint 不保证匹配这个 token。用：

```text
o
```

### 5550 / 5556 端口占用

```bash
ss -ltnp | grep -E ':5550|:5556|:5557|:5580'
```

按需清理：

```bash
pkill -f run_gr00t_server.py || true
pkill -f run_vla_inference.py || true
pkill -f pico_manager_thread_server.py || true
pkill -f g1_deploy_onnx_ref || true
```

### 相机无帧

PC 上：

```bash
cd ~/GR00T-WholeBodyControl

./tools/start_robot_realsense_camera.sh
```

然后再启动 viewer 或 VLA client。

### 手不动

确认机器人端 Inspire SDK 正在运行：

```bash
ssh unitree@192.168.123.164

ps aux | grep -E 'inspire_g1' | grep -v grep
```

确认 PC 手桥正在运行：

```bash
ps aux | grep -E 'inspire_hand_zmq_bridge' | grep -v grep
```
