# VLA demo 手动启动命令

当前手动链路需要 5 个终端：

1. 机器人端 camera server
2. PC 端 GR00T PolicyServer
3. PC 端 SONIC deploy
4. PC 端 VLA inference client
5. PC 端 keyboard publisher

终端 1、2、3 如果已经在跑，不要重复启动。

## 终端 1：机器人相机服务器

在机器人 `unitree@192.168.123.164` 上运行：

```bash
cd ~/GR00T-WholeBodyControl
source .venv_camera/bin/activate
python -m gear_sonic.camera.composed_camera --ego-view-camera usb --ego-view-device-id 5 --port 5555
```

## 终端 2：PC 启动 PolicyServer

```bash
cd ~/Isaac-GR00T

~/GR00T-WholeBodyControl/.venv_inference/bin/python \
  gr00t/eval/run_gr00t_server.py \
  --model-path ~/GR00T/models/huggingface_and_checkpoints/gr00t-n17-g1-grab-bottle-rh-371ep-v10-finetune/checkpoint-30000 \
  --embodiment-tag UNITREE_G1_SONIC \
  --device cuda:0 \
  --host 0.0.0.0 \
  --port 5550
```

看到下面输出后保持终端不关：

```text
Server is ready and listening on tcp://0.0.0.0:5550
```

## 终端 3：PC 启动 low-latency SONIC deploy

```bash
cd ~/GR00T-WholeBodyControl/gear_sonic_deploy

./deploy.sh \
  --cp policy/low_latency/model \
  --obs-config policy/low_latency/observation_config.yaml \
  --input-type zmq_manager \
  real
```

看到下面输出后说明 SONIC deploy 初始化完成，但还没有进入 control：

```text
Init Done
```

## 终端 4：PC 启动 VLA inference client

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

如果此时看到：

```text
waiting for state msg
```

是正常的。因为 SONIC deploy 还没有收到 `k` 命令进入 `CONTROL`，暂时不会发布 `g1_debug` 状态。

## 终端 5：PC 启动 keyboard publisher

复制下面这一整行：

```bash
cd ~/GR00T-WholeBodyControl && source .venv_inference/bin/activate && python -c 'import base64; exec(base64.b64decode("aW1wb3J0IHptcSx0aW1lCmN0eD16bXEuQ29udGV4dCgpCnB1Yj1jdHguc29ja2V0KHptcS5QVUIpCnB1Yi5iaW5kKCJ0Y3A6Ly9sb2NhbGhvc3Q6NTU4MCIpCnRpbWUuc2xlZXAoMC41KQpwcmludCgiS2V5Ym9hcmQgcHVibGlzaGVyIHJlYWR5LiBUeXBlIGsvaS9wIHRoZW4gRW50ZXIuIikKd2hpbGUgVHJ1ZToKICAgIGtleT1pbnB1dCgpCiAgICBwdWIuc2VuZF9zdHJpbmcoa2V5KQogICAgcHJpbnQoIlNlbnQ6Iiwga2V5KQo="))'
```

看到：

```text
Keyboard publisher ready. Type k/i/p then Enter.
```

之后所有按键都在这个终端 5 里输入，不是在 VLA client 终端输入。

## 执行顺序

第一次测试建议严格按这个顺序：

```text
k
i
p
p
k
```

含义：

| 输入 | 作用 |
|---|---|
| `k` | 启动 SONIC C++ control loop，进入 `CONTROL`，开始发布 `g1_debug` |
| `i` | 切到 POSE mode，并发送 initial pose |
| `p` | 恢复 VLA policy loop，开始执行 prompt |
| `p` | 暂停 VLA policy loop |
| `k` | 停止 SONIC C++ control loop |

推荐节奏：

```text
k
i
等待机器人稳定
p
运行 1-2 秒
p
k
```

## 修改 prompt

在终端 5 输入：

```text
t reach the box with the right hand
```

当前建议先使用温和 prompt：

```text
reach the bottle with the right hand
```

不要一开始使用：

```text
grab the bottle
pick up the bottle
walk to the bottle
```

## 安全注意

- 不要在机器人悬空时跑完整 VLA 闭环。
- 第一次只运行 1-2 秒。
- 如果动作趋势不对，先按 `p` 暂停，再按 `k` 停止。
- 当前机器人是假手，抓取类 prompt 不可靠，先做 reach 类验证。

## 对比测试：使用 default/release SONIC deploy 测试 initial pose

如果 low-latency 下按 `i` 后机器人明显前倾/歪站，可以测试 default/release SONIC 是否和 VLA initial token 更匹配。

注意：这个测试只验证 `k -> i`，不要按 `p` 启动 VLA。

### 1. 停掉当前 low-latency SONIC deploy

在当前 SONIC deploy 终端里按：

```text
Ctrl+C
```

如果进程没有退出，可以新开终端查：

```bash
ss -lntp | grep 5557
```

必要时杀掉旧 deploy 进程：

```bash
pkill -f g1_deploy_onnx_ref
```

### 2. 启动 default/release SONIC deploy

`deploy.sh` 默认就是：

```text
policy/release/model
policy/release/observation_config.yaml
```

所以不要加 low-latency 的 `--cp` 和 `--obs-config`。

```bash
cd ~/GR00T-WholeBodyControl/gear_sonic_deploy

./deploy.sh \
  --input-type zmq_manager \
  real
```

看到：

```text
Init Done
```

说明 release deploy 初始化完成。

### 3. 复用原来的 VLA client 和 keyboard publisher

如果终端 4 的 VLA client 还在跑，可以先保持。

如果已经退出，重新启动：

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

如果 keyboard publisher 不在跑，重新启动：

```bash
cd ~/GR00T-WholeBodyControl && source .venv_inference/bin/activate && python -c 'import base64; exec(base64.b64decode("aW1wb3J0IHptcSx0aW1lCmN0eD16bXEuQ29udGV4dCgpCnB1Yj1jdHguc29ja2V0KHptcS5QVUIpCnB1Yi5iaW5kKCJ0Y3A6Ly9sb2NhbGhvc3Q6NTU4MCIpCnRpbWUuc2xlZXAoMC41KQpwcmludCgiS2V5Ym9hcmQgcHVibGlzaGVyIHJlYWR5LiBUeXBlIGsvaS9wIHRoZW4gRW50ZXIuIikKd2hpbGUgVHJ1ZToKICAgIGtleT1pbnB1dCgpCiAgICBwdWIuc2VuZF9zdHJpbmcoa2V5KQogICAgcHJpbnQoIlNlbnQ6Iiwga2V5KQo="))'
```

### 4. 只测试 release initial pose

在 keyboard publisher 终端输入：

```text
k
```

确认机器人能站稳。

然后输入：

```text
i
```

观察 initial pose 是否还会：

- 双手明显向前折；
- 身体右前方前倾；
- 站姿明显歪。

不要按：

```text
p
```

### 5. 判断结果

| 结果 | 解释 |
|---|---|
| release 下 `i` 正常，low-latency 下 `i` 歪 | VLA/initial token 更可能和 release SONIC 对齐，不适合当前 low-latency deploy |
| release 和 low-latency 下 `i` 都歪 | hard-coded initial token 可能和当前安装的 SONIC checkpoint 都不匹配，需要重新找/生成 safe initial token |
| release 下 `i` 更差 | 不继续实机 VLA，回到源码/文档核对该 VLA checkpoint 训练时对应的 SONIC checkpoint |
