# SONIC + GR00T VLA 本地仓库迁移与 Git 管理说明

本文档用于把当前本机的 SONIC 控制器、VLA 采集、VLA 训练、VLA 实机部署代码整理成可迁移、可上传、可在另一台 PC 复现的代码仓库结构。

当前系统不是单仓库项目，而是两个源码仓库 + 若干外部模型/数据产物。

## 1. 本机目录结构

当前主要路径：

```text
/home/cenzo/GR00T/
├── repos/
│   ├── GR00T-WholeBodyControl/   # SONIC / 机器人控制 / 采集 / 实机部署
│   └── Isaac-GR00T/              # GR00T VLA 训练框架 / PolicyServer
├── models/                       # 模型和 checkpoint，不进 git
├── outputs/                      # 本机训练/测试输出，不进 git
├── data/                         # 数据集和本地数据，不进 git
├── runtime/                      # TensorRT 等运行时，不进 git
├── installers/                   # 本地安装包，可按需备份
└── logs/                         # 安装/运行日志，不进 git
```

两个源码仓库分别是：

```text
/home/cenzo/GR00T/repos/GR00T-WholeBodyControl
/home/cenzo/GR00T/repos/Isaac-GR00T
```

## 2. 两个源码仓库的职责划分

### 2.1 GR00T-WholeBodyControl

这个仓库负责真实机器人侧和 SONIC 侧：

- SONIC 低延迟控制器
- G1 真实机器人 deploy
- PICO manager / XRoboToolkit 读取
- RealSense 相机服务
- Inspire 灵巧手 SDK / bridge
- VLA 数据采集脚本
- VLA 实机推理 client
- 图像和机器人状态时间戳对齐
- 实机采集/部署命令文档

其中下面两个 VLA 脚本是原始 SONIC 仓库已有文件，不是新建文件：

```text
gear_sonic/scripts/run_data_exporter.py
gear_sonic/scripts/run_vla_inference.py
```

我们是在官方脚本基础上做了实机适配：

- `run_data_exporter.py`：增加图像和 proprio/state 时间戳对齐。
- `run_vla_inference.py`：增加部署时图像和状态对齐，以及低延迟 SONIC 下更安全的 `k -> o -> p` 启动链路。

### 2.2 Isaac-GR00T

这个仓库负责 VLA 训练和模型服务：

- Isaac-GR00T 官方训练框架
- VLA fine-tune
- PolicyServer
- GR00T 模型加载和推理服务
- TensorBoard / wandb logging

当前本地对 `Isaac-GR00T` 的源码改动很小，主要是允许通过环境变量控制训练日志输出：

```text
gr00t/experiment/experiment.py
```

改动目的：

```text
当 use_wandb=False 时，允许设置 GR00T_REPORT_TO=tensorboard，
从而在训练机上用 TensorBoard 看训练曲线。
```

## 3. 当前本地源码改动摘要

### 3.1 GR00T-WholeBodyControl 当前改动

当前有以下修改/新增文件需要纳入 git：

```text
M  VLA_DEMO_COMMANDS.md
M  gear_sonic/camera/composed_camera.py
M  gear_sonic/camera/sensor_server.py
M  gear_sonic/scripts/pico_manager_thread_server.py
M  gear_sonic/scripts/run_data_exporter.py
M  gear_sonic/scripts/run_vla_inference.py
M  gear_sonic_deploy/src/TRTInference/InferenceEngine.cpp
M  tools/realsense_color_zmq_server_py38.py

?? LOW_LATENCY_HAND_TELEOP.md
?? REPO_MIGRATION_GUIDE.md
?? VLA_DATA_COLLECTION_COMMANDS.md
?? VLA_DEMO_WORKFLOW.md
?? tools/debug_xrt_timestamp.py
?? tools/inspect_xrt_status.py
?? tools/mark_discarded_episodes.py
?? tools/start_robot_realsense_camera.sh
```

建议按功能拆 commit：

#### commit 1：相机服务和时间戳 debug

```text
gear_sonic/camera/composed_camera.py
gear_sonic/camera/sensor_server.py
tools/realsense_color_zmq_server_py38.py
tools/start_robot_realsense_camera.sh
```

建议 message：

```text
camera: add realsense timestamped low-latency service
```

#### commit 2：数据采集对齐

```text
gear_sonic/scripts/run_data_exporter.py
tools/mark_discarded_episodes.py
```

建议 message：

```text
data: align proprio frames to camera timestamps
```

#### commit 3：VLA 实机推理对齐和安全启动

```text
gear_sonic/scripts/run_vla_inference.py
```

建议 message：

```text
vla: add image-state sync and safe pose switch
```

#### commit 4：PICO manager / PoseLoop 低延迟诊断

```text
gear_sonic/scripts/pico_manager_thread_server.py
tools/debug_xrt_timestamp.py
tools/inspect_xrt_status.py
```

建议 message：

```text
teleop: add pico diagnostics and reduce manager idle contention
```

#### commit 5：部署兼容

```text
gear_sonic_deploy/src/TRTInference/InferenceEngine.cpp
```

建议 message：

```text
deploy: allow TensorRT cache fallback when ONNX is unavailable
```

#### commit 6：文档

```text
LOW_LATENCY_HAND_TELEOP.md
VLA_DATA_COLLECTION_COMMANDS.md
VLA_DEMO_COMMANDS.md
VLA_DEMO_WORKFLOW.md
REPO_MIGRATION_GUIDE.md
```

建议 message：

```text
docs: document sonic vla collection training and deployment
```

### 3.2 Isaac-GR00T 当前改动

当前只有一个文件：

```text
M gr00t/experiment/experiment.py
```

建议 commit：

```text
train: allow tensorboard reporting via environment variable
```

如果同时加入说明文档：

```text
SONIC_VLA_TRAINING_PATCHES.md
```

建议一起提交：

```text
docs: document sonic vla training patch
```

## 4. 不应该放进 git 的内容

以下内容不要上传到 GitHub/GitLab 源码仓库：

```text
.venv*/
outputs/
data/
models/
runtime/
logs/
camera_recordings/
teleop_vids/
*.mp4
*.parquet
*.safetensors
*.pt
*.pth
*.ckpt
*.onnx
*.trt
*.engine
```

当前两个仓库的 `.gitignore` 已经覆盖大部分大文件和产物目录。后续上传前仍建议执行：

```bash
git status --short
git diff --stat
git ls-files | grep -E '(\.mp4|\.parquet|\.safetensors|\.pt|\.onnx|\.trt|\.engine)$'
```

最后一个命令应该没有异常输出，除非是官方明确需要追踪的小模型文件。

## 5. 模型、checkpoint、数据集如何迁移

模型和数据不进 git，需要单独复制。

当前 VLA checkpoint 本机路径：

```text
/home/cenzo/GR00T/models/huggingface_and_checkpoints/sonic_vla_bottle_to_bin/
├── 10k-checkpoint-10000
├── 20k-checkpoint-16000
├── 20k-checkpoint-18000
└── 20k-checkpoint-20000
```

当前 cleaned 数据集示例：

```text
/home/cenzo/GR00T/repos/GR00T-WholeBodyControl/outputs/bottle_to_bin_cleaned
```

或者后续整理到：

```text
/home/cenzo/GR00T/data/datasets/bottle_to_bin_cleaned
```

推荐用 `rsync` 迁移：

```bash
rsync -avP \
  /home/cenzo/GR00T/models/huggingface_and_checkpoints/sonic_vla_bottle_to_bin/ \
  <user>@<new_pc>:/home/<user>/GR00T/models/huggingface_and_checkpoints/sonic_vla_bottle_to_bin/

rsync -avP \
  /home/cenzo/GR00T/repos/GR00T-WholeBodyControl/outputs/bottle_to_bin_cleaned/ \
  <user>@<new_pc>:/home/<user>/GR00T/data/datasets/bottle_to_bin_cleaned/
```

## 6. 远端仓库建议

建议两个源码仓库都 fork 成自己的云端仓库：

```text
GR00T-WholeBodyControl:
  upstream -> https://github.com/NVlabs/GR00T-WholeBodyControl.git
  origin   -> 你的云端 fork，例如 git@github.com:<org>/GR00T-WholeBodyControl-sonic-vla.git

Isaac-GR00T:
  upstream -> https://github.com/NVIDIA/Isaac-GR00T.git
  origin   -> 你的云端 fork，例如 git@github.com:<org>/Isaac-GR00T-sonic-vla.git
```

这样后续可以继续拉官方更新：

```bash
git fetch upstream
```

也可以推送自己的稳定版本：

```bash
git push origin main
```

## 7. 收到云端仓库地址后的上传流程

### 7.1 GR00T-WholeBodyControl

如果你给的是一个新的空仓库地址，例如：

```text
git@github.com:<org>/GR00T-WholeBodyControl-sonic-vla.git
```

则执行：

```bash
cd /home/cenzo/GR00T/repos/GR00T-WholeBodyControl

git remote rename origin upstream
git remote rename sonic-cloud origin

# 如果 sonic-cloud 不是目标仓库，则改成：
# git remote set-url origin git@github.com:<org>/GR00T-WholeBodyControl-sonic-vla.git

git remote -v
```

然后按功能提交并推送。

### 7.2 Isaac-GR00T

如果你给的是：

```text
git@github.com:<org>/Isaac-GR00T-sonic-vla.git
```

则执行：

```bash
cd /home/cenzo/GR00T/repos/Isaac-GR00T

git remote rename origin upstream
git remote add origin git@github.com:<org>/Isaac-GR00T-sonic-vla.git

git remote -v
```

然后提交训练侧 patch 并推送。

## 8. 另一台 PC 的部署流程

另一台 PC 上建议按如下结构：

```bash
mkdir -p ~/GR00T/repos
mkdir -p ~/GR00T/models/huggingface_and_checkpoints
mkdir -p ~/GR00T/data/datasets
```

clone 两个源码仓库：

```bash
cd ~/GR00T/repos

GIT_LFS_SKIP_SMUDGE=1 git clone git@github.com:<org>/GR00T-WholeBodyControl-sonic-vla.git
GIT_LFS_SKIP_SMUDGE=1 git clone git@github.com:<org>/Isaac-GR00T-sonic-vla.git
```

注意：这两个自建仓库主要保存源码改动和 LFS 指针，不建议把官方仓库里的所有 LFS 对象重新上传到自己的 GitHub LFS 配额里。clone 后需要把官方仓库设为 `upstream`，再从官方拉 LFS 对象。

`GR00T-WholeBodyControl`：

```bash
cd ~/GR00T/repos/GR00T-WholeBodyControl

git remote add upstream https://github.com/NVlabs/GR00T-WholeBodyControl.git
git lfs fetch upstream
git lfs checkout
```

`Isaac-GR00T`：

```bash
cd ~/GR00T/repos/Isaac-GR00T

git remote add upstream https://github.com/NVIDIA/Isaac-GR00T.git
git lfs fetch upstream
git lfs checkout
```

如果只做源码查看或轻量开发，可以先不执行 `git lfs fetch/checkout`。如果要真实部署、编译或运行官方示例，需要把相关 LFS 对象拉下来。

复制模型和 checkpoint：

```bash
rsync -avP <old_pc>:/home/cenzo/GR00T/models/huggingface_and_checkpoints/sonic_vla_bottle_to_bin/ \
  ~/GR00T/models/huggingface_and_checkpoints/sonic_vla_bottle_to_bin/
```

如果要训练，也复制数据集：

```bash
rsync -avP <old_pc>:/home/cenzo/GR00T/repos/GR00T-WholeBodyControl/outputs/bottle_to_bin_cleaned/ \
  ~/GR00T/data/datasets/bottle_to_bin_cleaned/
```

然后按文档启动：

```text
GR00T-WholeBodyControl/VLA_DATA_COLLECTION_COMMANDS.md
GR00T-WholeBodyControl/VLA_DEMO_COMMANDS.md
GR00T-WholeBodyControl/VLA_DEMO_WORKFLOW.md
```

## 9. 推荐保留的版本锁定信息

每次准备迁移到新 PC 或实机测试前，建议记录：

```bash
cd /home/cenzo/GR00T/repos/GR00T-WholeBodyControl
git rev-parse HEAD
git status --short

cd /home/cenzo/GR00T/repos/Isaac-GR00T
git rev-parse HEAD
git status --short
```

建议后续维护一个 `DEPLOY_LOCK.md`，记录：

```text
GR00T-WholeBodyControl commit:
Isaac-GR00T commit:
VLA checkpoint:
Robot IP:
Robot network interface:
Camera type:
Hand SDK version:
PICO/XRoboToolkit version:
```

这样另一台 PC 出问题时，可以先确认是不是源码版本、模型版本或机器人配置不一致。
