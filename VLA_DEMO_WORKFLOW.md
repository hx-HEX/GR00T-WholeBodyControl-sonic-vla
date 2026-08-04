# SONIC VLA demo：采集、训练、部署流程说明

本文档记录当前 `GR00T-WholeBodyControl` 仓库里，针对“桌面抓起水瓶，转向走到垃圾桶前并丢入”的 VLA demo 流程。目标是把数据采集、数据格式、数据筛选、训练和实机部署串成一条可复现链路。

更细的启动命令可参考：

- `VLA_DATA_COLLECTION_COMMANDS.md`：采集数据命令
- `VLA_DEMO_COMMANDS.md`：VLA 实机部署命令

## 1. 总体链路

当前流程分为四段：

1. 采集：PC 同时接收机器人相机、机器人状态、PICO/SONIC 遥操作动作，保存成 GR00T/LeRobot 风格数据集。
2. 筛选：人工看每个 episode 的视频，丢弃失败、异常、不可学习的 demo，生成 cleaned dataset。
3. 训练：把 cleaned dataset 放到多卡训练机，用 Isaac-GR00T 进行有效微调。
4. 部署：启动 SONIC 低延迟控制链路、GR00T PolicyServer 和 VLA client，让 VLA 根据相机图像和机器人状态输出动作。

当前实际任务是：

> 机器人在桌子前抓起一个水瓶，转向/行走到垃圾桶前，把水瓶丢进去。

## 2. 采集时记录的数据是什么

数据采集输出在 `outputs/<dataset_name>/` 下，格式接近 LeRobot/GR00T dataset。当前 cleaned 数据集示例：

```text
outputs/bottle_to_bin_cleaned/
├── meta/
│   ├── info.json
│   ├── modality.json
│   ├── stats.json
│   ├── relative_stats.json
│   ├── tasks.jsonl
│   └── episodes.jsonl
├── data/chunk-000/
│   └── episode_000000.parquet ...
└── videos/chunk-000/observation.images.ego_view/
    └── episode_000000.mp4 ...
```

当前 `bottle_to_bin_cleaned` 的规模：

- episode 数：51
- 总帧数：54738
- 采样 FPS：50
- 图像：`observation.images.ego_view`，480×640 RGB 视频，H.264 mp4
- 任务文本：当前 `tasks.jsonl` 里是 `demo`

每一帧核心字段包括：

| 字段 | 含义 |
| --- | --- |
| `observation.images.ego_view` | 机器人第一视角 RGB 图像 |
| `observation.state` | 43 维机器人关节状态：腿、腰、双臂、双手 |
| `observation.eef_state` | 14 维左右腕位姿：左腕位置/四元数、右腕位置/四元数 |
| `action.wbc` | 43 维 WBC 动作目标，和 `observation.state` 的关节顺序一致 |
| `observation.root_orientation` | 机身/root 姿态 |
| `observation.projected_gravity` | 投影重力，用于身体姿态感知 |
| `observation.cpp_rotation_offset` | C++ 控制侧旋转 offset |
| `observation.init_base_quat` | 初始 base 四元数 |
| `teleop.delta_heading` | 遥操作/规划方向变化 |
| `action.motion_token` | SONIC/C++ 侧 motion token，64 维 |
| `teleop.smpl_joints` | PICO/SONIC 侧 SMPL joints |
| `teleop.smpl_pose` | PICO/SONIC 侧 SMPL pose |
| `teleop.body_quat_w` | 身体朝向四元数 |
| `teleop.target_body_orientation` | 目标身体朝向，6D rotation |
| `teleop.left_hand_joints` / `teleop.right_hand_joints` | 双手关节目标 |
| `teleop.left_wrist_joints` / `teleop.right_wrist_joints` | 双腕目标 |
| `teleop.stream_mode` | 当前 SONIC stream mode，例如 PLANNER/POSE |
| `teleop.planner_*` | planner 模式、移动方向、朝向、速度、高度 |
| `teleop.vr_3pt_position` / `teleop.vr_3pt_orientation` | PICO 头/手三点追踪 |
| `timestamp` | 采集侧时间戳 |
| `frame_index` / `episode_index` / `index` / `task_index` | 数据集索引 |

数据采集时，图像和 proprio/state 不是简单取“当前最新状态”，而是按时间戳做最近邻匹配：

- 相机服务在机器人端发布图像和图像时间戳。
- PC 端收到 proprio/state 后缓存一段时间。
- 每次写入图像帧时，选择和该图像时间戳最接近的 proprio/state。
- debug 会输出类似 `[Sync] image↔proprio dt=...ms`，用于确认图像和状态的对齐误差。

这部分对齐主要影响数据集质量；debug 里的相机 latency 只是用来观察链路延迟，不是训练字段本身。

## 3. 数据采集流程

### 3.1 采集前准备

采集前需要确认：

- 机器人和 PC 在同一网络，机器人 IP 当前按 `192.168.123.164` 使用。
- RealSense 相机服务可启动。
- Inspire 灵巧手 SDK/bridge 可启动。
- PICO 已连接 XRoboToolkit，PC 端能读到 PICO body/hand 数据。
- SONIC 低延迟 deploy 能正常进入 PLANNER/POSE。
- 桌面、水瓶、垃圾桶的位置相对稳定，任务开始状态一致。

### 3.2 推荐启动顺序

完整命令以 `VLA_DATA_COLLECTION_COMMANDS.md` 为准。高层顺序如下：

1. 启动机器人 RealSense 相机服务，同时重启机器人端灵巧手 SDK：

```bash
cd ~/GR00T-WholeBodyControl
./tools/start_robot_realsense_camera.sh
```

2. 启动 SONIC 低延迟 deploy。

使用低延迟版本，不要混用默认 10 帧缓存版本。当前建议使用 `zmq_manager` 输入，低延迟 planner/pose 链路，具体命令见 `VLA_DATA_COLLECTION_COMMANDS.md`。

3. 启动 PICO manager。

当前推荐保留 `--manager-idle-hz 50`，避免 OFF/PLANNER 状态时 Python 忙等影响 PICO reader：

```bash
cd ~/GR00T-WholeBodyControl
source .venv_teleop/bin/activate

python gear_sonic/scripts/pico_manager_thread_server.py \
  --manager \
  --target_fps 45 \
  --manager-idle-hz 50
```

如果要排查 PICO/pose 延迟，再额外加 profiling 参数：

```bash
  --profile-pico-reader \
  --pico-anomaly-dt-ms 50 \
  --pico-no-data-warn-ms 30 \
  --pico-read-slow-ms 5 \
  --profile-pose-loop \
  --profile-pose-threshold-ms 20
```

4. 启动手部 ZMQ bridge：

```bash
cd ~/GR00T-WholeBodyControl
tools/build/inspire_hand_zmq_bridge \
  --iface enp6s0 \
  --max-close 0.8
```

5. 启动数据采集/exporter：

```bash
cd ~/GR00T-WholeBodyControl
source .venv_data_collection/bin/activate

python gear_sonic/scripts/run_data_exporter.py \
  --camera-host 192.168.123.164 \
  --camera-port 5555
```

### 3.3 采集时的 PICO 操作

常用按键：

- `A+B+X+Y`：启动/停止 SONIC policy。
- `A+X`：切换 PLANNER/POSE。
- 左/右 trigger：控制对应灵巧手闭合。
- 左 grip + `A`：开始/停止录制一个 episode。
- 左 grip + `B`：丢弃当前 episode。

采集时建议先进入稳定站姿，再开始录制。录制 episode 要覆盖完整任务闭环：

1. 机器人站在桌前，水瓶在视野内。
2. 手臂接近水瓶。
3. 成功抓取水瓶。
4. 转向并走到垃圾桶附近。
5. 对准垃圾桶。
6. 张手释放水瓶。
7. 水瓶进入垃圾桶，机器人保持稳定。

## 4. 什么样的数据应该保留

保留的数据应该是“VLA 能从图像和状态中学到稳定因果关系”的 episode。建议保留：

- 任务完成：水瓶确实被抓起并丢入垃圾桶。
- 图像可见：水瓶、手、桌面、垃圾桶关键阶段尽量在 `ego_view` 中可见。
- 轨迹连续：没有长时间停顿、异常抖动、明显人工纠偏。
- 手部动作正确：抓取阶段闭手，释放阶段开手，手部控制没有卡死。
- 机器人稳定：无明显摔倒、脚踝过热导致动作异常、突然保护停机。
- 起点和目标变化可控：可以有少量位置变化，但不要在数据很少时引入过大分布差异。

建议丢弃：

- 抓取失败、没抓住水瓶。
- 水瓶掉落但未进垃圾桶。
- 走路/转向中机器人明显失稳。
- PICO 追踪丢失导致手腕/身体姿态跳变。
- 相机画面严重遮挡、模糊或关键物体不在视野内。
- 灵巧手没启动、没响应或一直保持错误闭合状态。
- episode 开始/结束时包含大量无关等待。

当前已经有一个 cleaned dataset：`outputs/bottle_to_bin_cleaned`。之前人工筛选时，丢弃了多个原始采集 session 中的失败 episode，并将可用样本整理成 51 个 episode。

## 5. 数据筛选与整理

推荐流程：

1. 先看每个 episode 的 `observation.images.ego_view` mp4。
2. 记录要丢弃的 episode index。
3. 用筛选脚本生成 cleaned dataset。
4. 检查 cleaned dataset 的 `meta/info.json`、`meta/episodes.jsonl` 和视频数量是否一致。
5. 抽查 cleaned 后的视频，确认 episode index 没错。

已有脚本：

```bash
tools/mark_discarded_episodes.py
```

筛选的目标不是“只保留最好看的轨迹”，而是保留任务成功、视觉/状态/动作一致、没有明显系统异常的轨迹。少量自然速度差异和轻微姿态差异是有价值的，但失败轨迹在当前小数据量阶段会明显污染训练。

## 6. 训练流程

### 6.1 训练输入

训练输入是 cleaned dataset，例如：

```text
outputs/bottle_to_bin_cleaned/
```

上传到训练机后，当前使用过的路径是：

```text
/workspace/sonic_vla_cenzo/datasets/bottle_to_bin_cleaned
```

训练 repo：

```text
/workspace/sonic_vla_cenzo/repos/Isaac-GR00T
```

基础模型：

```text
/workspace/sonic_vla_cenzo/models/Cosmos-Reason2-2B-full
```

### 6.2 训练做了什么

训练使用 Isaac-GR00T 的 VLA fine-tune 流程，把“图像 + 机器人状态 + 语言任务”映射到机器人动作。当前有效微调不是只训 projector-only，而是训练：

- diffusion/action head
- projector / multimodal adapter

同时冻结大部分 VLM backbone。之前训练日志中 trainable 参数约 1.62B，占总参数约 51.54%。这比本机 5090 上的 projector-only 测试更有意义。

训练目标可以理解为：

```text
(ego_view 图像, proprio/state, language prompt)
    -> 未来动作序列 / action chunk
```

也就是说，模型学习的是在当前视觉和机器人状态下，下一段应该如何移动身体、手臂和灵巧手来完成“抓瓶并丢入垃圾桶”。

### 6.3 训练配置与 checkpoint

当前已跑过：

```text
10k:
/workspace/sonic_vla_cenzo/outputs/bottle_to_bin_n1p7_4gpu_effective/checkpoint-10000

20k:
/workspace/sonic_vla_cenzo/outputs/bottle_to_bin_n1p7_4gpu_effective_20k/checkpoint-16000
/workspace/sonic_vla_cenzo/outputs/bottle_to_bin_n1p7_4gpu_effective_20k/checkpoint-18000
/workspace/sonic_vla_cenzo/outputs/bottle_to_bin_n1p7_4gpu_effective_20k/checkpoint-20000
```

已拷贝到本机：

```text
~/GR00T/models/huggingface_and_checkpoints/sonic_vla_bottle_to_bin/
├── 10k-checkpoint-10000
├── 20k-checkpoint-16000
├── 20k-checkpoint-18000
└── 20k-checkpoint-20000
```

### 6.4 训练资源建议

当前这个 VLA 有效微调建议使用多卡服务器。原因：

- 模型本身较大。
- 有效微调包含 diffusion/action head 和 projector，不是只训一个很小的头。
- 单张 5090 可以做很小规模验证或 projector-only 测试，但那种训练对实机任务帮助有限。

实际建议：

- 优先：4 卡训练机。
- 本机 5090：只用于快速检查数据读取、脚本是否能跑通，不作为最终有效训练方案。

### 6.5 曲线查看

训练机上查看 TensorBoard：

```bash
cd /workspace/sonic_vla_cenzo/repos/Isaac-GR00T

/workspace/sonic_vla_cenzo/repos/Isaac-GR00T/.venv/bin/tensorboard \
  --logdir /workspace/sonic_vla_cenzo/outputs \
  --host 127.0.0.1 \
  --port 6006
```

然后从本机做 SSH 端口转发：

```bash
ssh -L 6006:127.0.0.1:6006 root@<训练机IP>
```

浏览器打开：

```text
http://127.0.0.1:6006
```

注意：当前曲线指标较少，主要用于确认 loss 是否正常下降、训练是否发散、checkpoint 是否按预期保存。最终是否可用仍需要实机验证。

## 7. 实机部署流程

部署阶段和采集类似，也需要机器人侧相机、SONIC、PICO manager、手部 bridge，但额外需要启动 VLA PolicyServer 和 VLA inference client。

### 7.1 启动基础链路

1. 启动机器人相机和手 SDK：

```bash
cd ~/GR00T-WholeBodyControl
./tools/start_robot_realsense_camera.sh
```

2. 启动 SONIC 低延迟 deploy。

3. 启动 PICO manager：

```bash
cd ~/GR00T-WholeBodyControl
source .venv_teleop/bin/activate

python gear_sonic/scripts/pico_manager_thread_server.py \
  --manager \
  --target_fps 45 \
  --manager-idle-hz 50
```

4. 启动 hand bridge：

```bash
cd ~/GR00T-WholeBodyControl
tools/build/inspire_hand_zmq_bridge \
  --iface enp6s0 \
  --max-close 0.8
```

### 7.2 启动 VLA server 和 client

具体命令以 `VLA_DEMO_COMMANDS.md` 为准。核心点：

- PolicyServer 加载某个 checkpoint，例如 `20k-checkpoint-20000`。
- VLA client 连接 robot camera、机器人状态和 PolicyServer。
- VLA client 推荐使用 `--sync-state-to-image`，让部署时也按图像时间戳选择最近的 proprio/state。

部署时目前推荐的控制顺序：

```text
k -> o -> p
```

含义：

- `k`：让 VLA client 进入 idle/准备状态。
- `o`：让 C++/SONIC 从 PLANNER 切到 POSE，但不发送低延迟链路不匹配的 initial token。
- `p`：开始让 VLA policy 输出动作。

当前低延迟 SONIC 不建议直接用 `i`，因为 `i` 会发送 `LATENT_INITIAL_MOTION_TOKEN`，该 token 和当前低延迟默认姿态/启动状态可能不匹配，存在摔倒风险。

## 8. 验证建议

建议按风险从低到高验证：

1. 只看相机：

```bash
cd ~/GR00T-WholeBodyControl
source .venv_data_collection/bin/activate

python gear_sonic/scripts/run_camera_viewer.py \
  --camera-host 192.168.123.164 \
  --camera-port 5555
```

2. 只启动 PICO manager，看 PICO reader 是否稳定。
3. 启动 SONIC，但先不要进入 POSE。
4. 用 PICO 手动遥操作一小段，确认手和身体都正常。
5. 启动 VLA server/client，但先不按 `p`。
6. 短时间按 `p` 验证 1-2 秒动作，再停。
7. 先试 10k checkpoint，再试 20k checkpoint，对比稳定性和任务完成度。

实机验证时重点观察：

- 机器人是否维持稳定站姿。
- 手是否能正确开合。
- 视觉中的水瓶/垃圾桶是否和训练分布一致。
- 右脚踝温度是否持续升高。
- SONIC 日志里是否有异常大的 `Streaming data mean delay`。
- PICO reader 是否出现长时间 timestamp gap/stall。

## 9. 当前代码相对原始源码的关键改动

和 VLA 采训推直接相关的改动主要有：

1. 数据采集对齐：
   - `gear_sonic/scripts/run_data_exporter.py`
   - 增加 proprio/state buffer。
   - 写入图像帧时选择最接近图像时间戳的 proprio/state。
   - 增加 `[Sync] image↔proprio dt=...` debug。

2. VLA 推理对齐：
   - `gear_sonic/scripts/run_vla_inference.py`
   - 增加 `--sync-state-to-image`。
   - 推理时可选择和图像时间戳最近的状态，而不是直接读最新状态。

3. VLA 部署控制：
   - `gear_sonic/scripts/run_vla_inference.py`
   - 增加键盘命令 `o`，用于切到 POSE 但不发送 initial token。
   - 当前低延迟 SONIC 推荐 `k -> o -> p`，避免 `i` 的 token 不匹配风险。

4. 相机时间戳和 latency debug：
   - `gear_sonic/camera/sensor_server.py`
   - `gear_sonic/camera/composed_camera.py`
   - `tools/realsense_color_zmq_server_py38.py`
   - 增加图像 debug timestamps，用于拆分 `capture→read`、`read→publish`、`publish→client`。

5. PICO/SONIC 低延迟稳定性：
   - `gear_sonic/scripts/pico_manager_thread_server.py`
   - 增加 PICO reader diagnostic。
   - 增加 `--manager-idle-hz`，降低 manager idle 忙等。
   - 延后 PicoReader 到 manager 初始化后启动。
   - 增加 PoseLoop profiling，用于定位 `stale_timestamp`、`wait_target_timestamp` 等异常。

6. 模型/部署兼容：
   - `gear_sonic_deploy/src/TRTInference/InferenceEngine.cpp`
   - 当 ONNX 缺失时允许回退到已有 TRT cache，便于部署端启动。

## 10. 当前已知限制和后续 TODO

- 目前 cleaned dataset 只有 51 个 episode，适合作为初版 demo，不应期望直接泛化到很多瓶子、桌面、垃圾桶位置。
- 当前任务文本还是 `demo`，后续建议改成明确语言指令，例如 `pick up the water bottle and drop it into the trash bin`。
- 部署推理的状态对齐现在是参数可选，建议实机默认开启 `--sync-state-to-image`。
- PICO 偶发 timestamp stall 仍需继续观察，但当前已确认稳定情况下 PICO reader 可达到约 90 Hz。
- 相机 `publish→client` latency 仍可能偏大，训练数据对齐可以缓解数据质量问题，但不能让实机闭环本身变成零延迟。
- 后续如果要提高成功率，需要增加成功 demo 数量，并覆盖适度变化：瓶子位置、抓取角度、垃圾桶相对位置、机器人起点姿态。

