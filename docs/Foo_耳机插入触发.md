# Foo —— 插耳机就放歌的玩笑程序

> 记录日期：2026-09-23
> 程序：`tools/foo.py`

---

## ⚠️ 先说清楚：这不是正式项目内容

**Foo 是做着玩的小玩具，不属于「会听会变声的智能终端」的交付内容。**

- ❌ 不要把它编进正式程序
- ❌ 不要把它写进答辩材料当功能点
- ✅ 它存在的意义是：好玩 + 顺手验证了「耳机插入检测」这条硬件通路

不过下面记录的**硬件结论是真实有效的**（尤其是第 4 节那个负面结论），
以后正式程序如果需要"插耳机自动切换输出"之类的能力，可以直接复用。

---

## 1. 它做什么

```
启动              →  屏幕显示开屏画面
插入 3.5mm 插头   →  播放音乐 + 屏幕切到照片
拔出插头          →  停止播放，回到开屏
音乐自然放完      →  自动回到开屏
                      （此时插头还插着也不会自动重播，必须拔出再插入）
```

---

## 2. 硬件：耳机插入检测是怎么接的

设备树里（`rk3588-rock-5b.dtsi` 的 `analog-sound` 节点）：

```dts
analog-sound {
    compatible = "audio-graph-card";
    label = "rk3588-es8316";
    hp-det-gpios = <&gpio1 RK_PD5 GPIO_ACTIVE_HIGH>;
};
```

| 项 | 值 |
|---|---|
| GPIO | `gpiochip1` 第 **29** 线（GPIO1_D5） |
| 线名 | `hp-det` |
| 状态 | **被声卡驱动占用**（`gpioinfo` 显示 `[used]`）→ **用户态不能直接 gpiod 读** |

> ⚠️ 所以**不要**试图用 libgpiod 去 request 它，会拿到 "Resource busy"。

---

## 3. 可用方案：驱动导出的两个用户态接口

### ① 输入设备事件（Foo 用它做触发）

```
设备名  : rockchip-es8316 Headset
节点    : /dev/input/event8
能力    : EV_SW，上报 SW_HEADPHONE_INSERT (code=2)
取值    : value=1 → 插入     value=0 → 拔出
```

**事件驱动、零延迟。**

### ② ALSA 控制（Foo 用它读初值）

```
numid=38   iface=CARD   name='Headphone Jack'     type=BOOLEAN  access=r-------
numid=39   iface=CARD   name='Headset Mic Jack'   type=BOOLEAN  access=r-------

读取： amixer -c 4 cget numid=38     →  : values=on / off
```

**给出绝对状态**（"当前插着没有"），而事件只知道"变了"。
所以启动时读一次对齐初值。

> 💡 **声卡号会漂移**（实测见过 4 → 2 → 4）。正式程序要用的话，
> 应该按卡名 `rockchipes8316` 去找，不要写死数字。

### 分工

```
启动时  →  读 ALSA numid=38 得到绝对状态
运行中  →  监听 /dev/input/event8 的 SW 事件做触发（零延迟、不轮询）
```

---

## 4. ⚠️ 重要负面结论：板载 KEY 按钮**用不了**

这条花了很多时间才确认，**记下来避免以后重走弯路**。

### 4.1 系统里它不存在

`/proc/bus/input/devices` 里只有：

```
rk805 pwrkey             /dev/input/event0   ← 电源键（PMIC）
headset-keys             /dev/input/event7   ← 耳机线控
rockchip-es8316 Headset  /dev/input/event8
…以及 HDMI CEC、各声卡的插孔设备
```

**没有任何一个对应板载 KEY 按钮。**

### 4.2 设备树里也没有

把**主线内核**里 ROCK 5B+ 的三个设备树文件全读过：

| 文件 | 内容 |
|---|---|
| `rk3588-rock-5b-plus.dts` | 只有 M.2 的 GPIO hog，**无按键** |
| `rk3588-rock-5b.dtsi` | 只有 LEDs / rfkill / 声卡，**无按键** |
| `rk3588-rock-5b-5bp-5t.dtsi` | 电源键走 **RK806 PMIC**；MASKROM 是 **SoC 专用脚** |

板子自己的 `/proc/device-tree` 也搜过，`*key*` / `*maskrom*` / `*recover*` **全部为空**。

**结论：Linux 官方从未把这块板子的 KEY 按钮定义成任何输入设备。**

### 4.3 扫描结果的误导性（重要教训）

写了 GPIO 扫描器（`tools/find_button.py`）遍历所有空闲 GPIO。
扫描时 `gpiochip3` 第 5 线会跟着按键跳动，**6 次按下 + 6 次松开完全吻合**。

**但把它接进程序后，无论边沿中断还是轮询，都收不到任何事件。**

推测原因：扫描器当时**同时持有全部 141 条 GPIO 的请求并打开了内部上拉**，
这个整体状态可能影响了该引脚的电路；单独持有时行为就不同了。

> 📌 **教训**：扫描出来的"候选"必须用**隔离的最小程序**复验，不能直接当结论。

> 💡 顺带记住这个坑：不带内部上拉直接读这些 GPIO 会被**浮空噪声**淹没 ——
> 第一版扫描就是这么失败的（40 秒里同一根线乱跳 20 多次）。
> **打开 `LINE_REQ_FLAG_BIAS_PULL_UP` 之后噪声才消失。**

### 4.4 MASKROM 按钮

- **系统运行时按它什么都不会发生** —— 它是 BootROM 在**开机瞬间**采样的专用脚
- **⚠️ 危险**：如果按住它的同时断电/重启，板子会进入 MASKROM 模式（**不启动系统**），
  表现成"开不了机"，必须**松开按键后重新上电**才能恢复
- **比赛中途误触导致进 MASKROM 是很致命的故障** —— 演示时远离这个按钮

---

## 5. 运行方法

```bash
cd ~/proj/kskbl

# 正式跑
python3 -u tools/foo.py

# 只测插拔，不碰屏幕和音频（排查用）
python3 -u tools/foo.py --test-jack

# 换素材
python3 -u tools/foo.py --mp3 /path/song.mp3 --image /path/photo.png
```

> 💡 加 `-u` 是为了不缓冲输出 —— 重定向到日志文件后能实时看到状态变化。

### 后台独立运行（脱离 SSH 会话）

```bash
cd ~/proj/kskbl
setsid nohup python3 -u tools/foo.py > /tmp/foo.log 2>&1 < /dev/null &
```

这样网络抖动导致 SSH 断开时，程序**不会被连带杀掉**。之后 `cat /tmp/foo.log` 看日志。

> ⚠️ **踩过的坑**：不要用 `pkill -f demo_player.py` 这类命令去杀它 ——
> 如果命令行里恰好出现同样的路径字符串，`pkill -f` 会**把执行命令的 shell 自己杀掉**。
> 用方括号技巧（`tools/foo[.]py`）或直接按 PID 杀。

---

## 6. 权限要求

| 需求 | 命令 | 说明 |
|---|---|---|
| 读 `/dev/input/event8` | `sudo usermod -aG input radxa` | 节点属 `root:input`；**执行后需重新登录**才生效 |
| 读 ALSA 插孔状态 | 无需额外操作 | 节点在 `audio` 组，radxa 已在 |
| 屏幕 SPI / GPIO | 无需额外操作 | radxa 已在 `spidev` / `gpio` 组 |

> ✅ **不需要**改 `logind.conf`，**不需要**碰 PWRKEY。

---

## 7. 关键参数速查

| 项 | 值 |
|---|---|
| 开屏设计 | 复用 `tools/screen_bringup.py` 的 `make_text()` |
| 照片缩放 | **contain**（等比缩小完整放入，留黑边）—— 282×320 → 212×240 |
| SPI 速率 | **30 MHz**（25.5 fps） |
| 音频播放 | `cvlc --intf dummy --play-and-exit`（子进程，不阻塞屏幕） |
| 插孔事件 | `/dev/input/event8`，`EV_SW` / `SW_HEADPHONE_INSERT`(2) |
| 插孔状态 | `amixer -c 4 cget numid=38` → `values=on/off` |

---

## 8. 实机验证记录（2026-09-23）

```
🎧 检测到拔出（本来就空闲，忽略）
🎧 检测到插入 → 开始播放，屏幕已切到图片
🎧 检测到拔出 → 停止播放，回到开屏
```

| # | 检查项 | 结果 |
|---|---|---|
| 1 | 插孔事件捕获 | ✅ 插拔 4 插 3 拔，事件与操作时间戳完全一致 |
| 2 | ALSA 状态同步 | ✅ `Headphone Jack` 跟着 on/off |
| 3 | 开屏显示 | ✅ 启动即显示 |
| 4 | 插入 → 播放 + 切图 | ✅ |
| 5 | 拔出 → 停止 + 回开屏 | ✅ |
| 6 | 空闲时拔出被正确忽略 | ✅ |
