# VLA 数据采集完整启动命令

这份文档记录当前机器人的 VLA 数据采集启动顺序：

- 低延迟 SONIC real deploy
- PICO 全身遥操
- Inspire/RH56 灵巧手
- 机器人 RealSense D435i 相机
- PC 端 VLA data exporter

默认假设：

- 机器人 IP：`192.168.123.164`
- 机器人用户：`unitree`
- PC 机器人有线网口：`enp6s0`
- PC 工作目录：`~/GR00T-WholeBodyControl`
- TensorRT 路径：`~/TensorRT`

## 终端 1：PC，启动机器人相机和 Inspire 手 SDK

```bash
cd ~/GR00T-WholeBodyControl

./tools/start_robot_realsense_camera.sh
```

期望看到：

```text
[hand] Inspire hand SDK started.
Sensor server running at tcp://*:5555
```

如果只想启动相机，不启动 Inspire 手 SDK：

```bash
cd ~/GR00T-WholeBodyControl

START_INSPIRE_HAND_SDK=0 ./tools/start_robot_realsense_camera.sh
```

## 终端 2：PC，启动低延迟 SONIC deploy

```bash
cd ~/GR00T-WholeBodyControl/gear_sonic_deploy

export TensorRT_ROOT=$HOME/TensorRT

./deploy.sh \
  --cp policy/low_latency/model \
  --obs-config policy/low_latency/observation_config.yaml \
  --input-type zmq_manager \
  real
```

提示是否启动真机控制时输入：

```bash
y
```

期望看到：

```text
Realtime debug socket bound to port: 5557
Init Done
```

注意：采集或遥操时不要打开 deploy 侧的 `--zmq-verbose`，否则 `ZMQEndpointInterface` 会每帧打印大量解析日志，可能影响实时性。

## 终端 3：PC，启动 PICO manager

推荐采集时使用带异常诊断的版本：

```bash
cd ~/GR00T-WholeBodyControl

source .venv_teleop/bin/activate

python gear_sonic/scripts/pico_manager_thread_server.py \
  --manager \
  --target_fps 45 \
  --profile-pose-loop \
  --profile-pose-threshold-ms 30
```

正常情况下会看到：

```text
Manager controls: A+X=toggle mode, A+B+X+Y=start/stop policy
[PicoReader] dt_ts: ...
[PoseLoop] FPS: ...
```

如果出现：

```text
[PoseLoop DROP] ...
```

说明 5 秒统计窗口内 PoseLoop 输出低于目标频率，需要关注其中的 `skips`：

- `stale_timestamp`：PoseLoop 反复读到同一个 PICO 时间戳。
- `wait_target_timestamp`：PICO 时间戳还没推进到下一个输出时间点。
- `no_sample`：没有拿到 PICO 样本。
- `init_timestamp`：刚进入 POSE 或刚重置缓冲，短时间出现是正常的。

如果不需要诊断输出，可以用普通版本：

```bash
cd ~/GR00T-WholeBodyControl

source .venv_teleop/bin/activate

python gear_sonic/scripts/pico_manager_thread_server.py \
  --manager \
  --target_fps 45
```

## 终端 4：PC，启动 Inspire 手桥接

```bash
cd ~/GR00T-WholeBodyControl

tools/build/inspire_hand_zmq_bridge \
  --iface enp6s0 \
  --max-close 0.8
```

如果左右手反了，用：

```bash
cd ~/GR00T-WholeBodyControl

tools/build/inspire_hand_zmq_bridge \
  --iface enp6s0 \
  --max-close 0.8 \
  --swap-hands
```

## 终端 5：PC，启动 VLA data exporter

```bash
cd ~/GR00T-WholeBodyControl

source .venv_data_collection/bin/activate

python gear_sonic/scripts/run_data_exporter.py \
  --camera-host 192.168.123.164 \
  --camera-port 5555
```

如果 exporter 一直显示：

```text
[Config] Waiting for robot_config on tcp://localhost:5557 ...
```

说明终端 2 的 SONIC deploy 没有正常运行，或者还没有发布 `robot_config`。

如果显示：

```text
Waiting for message. Avail msg: proprio False | image True
```

通常说明 SONIC deploy 还没进入控制状态。按 PICO 的 `A+B+X+Y` 启动 policy 后再看。

## PICO 操作

```text
A+B+X+Y      启动 / 停止 SONIC policy
A+X          切换 PLANNER / POSE
POSE         全身遥操
左/右扳机    控制左/右 Inspire 手闭合

左 grip + A  开始 / 结束录制一条 episode
左 grip + B  放弃当前 episode
```

## 推荐采集流程

1. 启动终端 1 到终端 5。
2. 机器人稳定站立。
3. PICO 按 `A+B+X+Y` 启动 SONIC policy。
4. 按 `A+X` 进入 `POSE`。
5. 等 `PoseLoop FPS` 稳定。
6. 确认 exporter 里 image/proprio 都正常。
7. 用 `左 grip + A` 开始录制。
8. 完成一次任务后，再按 `左 grip + A` 停止并保存。
9. 如果这条失败，用 `左 grip + B` 放弃当前 episode。

## 相机可视化检查

采集前可以单独检查相机画面：

```bash
cd ~/GR00T-WholeBodyControl

source .venv_data_collection/bin/activate

python gear_sonic/scripts/run_camera_viewer.py \
  --camera-host 192.168.123.164 \
  --camera-port 5555
```

注意：正式采集时建议关闭 camera viewer，避免额外占用图像链路和显示资源。

## 关键注意事项

- 不要在 deploy 侧打开 `--zmq-verbose` 做正式遥操或采集。
- 如果右脚踝温度持续超过 90℃，停止采集并让机器人冷却。
- 如果 `PicoReader dt_ts` 出现几十到几百毫秒跳变，优先检查 PICO 电量、PICO 温度、XRoboToolkit PC Service 和 WiFi 网络。
- 如果 `PoseLoop DROP` 主要是 `stale_timestamp`，说明 PICO 时间戳更新不连续或 Python 读取到了重复帧。
- 数据采集时，任务起始状态尽量一致：物体位置、机器人站位、视野范围和动作目标都要保持可复现。
