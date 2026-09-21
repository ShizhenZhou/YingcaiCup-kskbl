# ROCK 5B+ 音频子系统笔记（ES8316）

> 记录日期：2026-09-21
> 适用板卡：ROCK 5B+（RS129-D16E0）
> 系统：Radxa OS / Debian 12 bookworm / kernel `6.1.84-8-rk2410` / aarch64
> 本文所有结论均为**在实机上实测得出**，不是从文档推断的。

---

## 0. 一句话结论

**音频输出与采集链路都已验证可用。** 但 Radxa 镜像自带的 ALSA UCM 配置与本版厂商内核的控件命名不匹配，导致耳机通路在开机时**从未被正确初始化**。本文给出正确的控件名与初始化命令。

---

## 1. 硬件与设备标识

| 项目 | 值 |
|---|---|
| 编解码器 | Everest **ES8316** |
| ALSA 卡名 | **`rockchipes8316`** |
| PipeWire 节点 | `alsa_output.platform-es8316-sound.stereo-fallback` |
| 3.5mm 接口 | **四段复合孔**（耳机 + 麦克风），支持耳机插入检测 |
| 其他声卡 | `rockchiphdmi0` / `rockchiphdmi1` / `rockchiphdmi2` / `rockchiphdmiin`（共 5 张卡） |

`aplay -l` / `arecord -l` 均能看到 card `rockchipes8316` device 0（播放与录音同一个 PCM）。

---

## 2. ⚠️ 铁律一：永远用卡名，绝不用卡号

**Linux 声卡编号取决于驱动探测顺序，重启后会漂移。** 实测记录：

| | card 0 | card 1 | card 2 | card 3 | card 4 |
|---|---|---|---|---|---|
| 第一次开机 | hdmi0 | hdmi1 | hdmi2 | hdmiin | **es8316** |
| 重启后 | hdmi0 | hdmi1 | **es8316** | hdmi2 | hdmiin |

所以 `amixer -c 4 ...` 在重启后会失效，报错：

```
amixer: Unable to find simple control 'hp switch',0
```

**正确写法（两种都行）：**

```bash
amixer -c rockchipes8316 ...            # 用卡名
amixer -c rockchipes8316 cget numid=42  # 或干脆用 numid
```

> 附带好处：`/var/lib/alsa/asound.state` 是**按卡名**存储的（`state.rockchipes8316 { ... }`），所以 `alsactl` 的存取本身不受编号漂移影响，只有手敲的命令会踩坑。

---

## 3. ⚠️ 铁律二：`amixer sget` 看不到真正的控件名

这是本项目最容易踩的坑，我在这里错了两次。

`amixer` 有**两套视图**：

| 命令 | 看的是 | 名字形态 |
|---|---|---|
| `amixer scontrols` / `sget` / `sset` | **simple mixer control**（简化视图） | 短名：`Headphone`、`hp switch`、`DAC` |
| `amixer controls` / `cget` / `cset` | **全部 kcontrol** | 全名：`Headphone Playback Volume`、`Headphone Switch` |

**UCM 配置里用的是全名**，所以必须用 `cget` / `cset` 访问。

错误示范（会误判为"控件不存在"）：

```bash
amixer -c rockchipes8316 sget 'Headphone Playback Volume'
# → amixer: Unable to find simple control 'Headphone Playback Volume',0
```

正确写法：

```bash
amixer -c rockchipes8316 cget iface=MIXER,name='Headphone Playback Volume'
amixer -c rockchipes8316 cset iface=MIXER,name='Headphone Switch' on
```

---

## 4. ES8316 完整控件表（本版内核实测）

用 `amixer -c rockchipes8316 controls` 导出。**加粗**的是音频通路相关的关键控件。

| numid | 全名 | 类型 / 范围 | 说明 |
|---|---|---|---|
| 12 | **Headphone Playback Volume** | INT 0–3 | 0 = −48dB，3 = 0dB。**档位极粗，只应设为 3（0dB）**，音量交给软件 |
| 13 | **Headphone Mixer Volume** | INT 0–11 | 0 = −12dB，11 = 0dB |
| 15 | **DAC Playback Volume** | INT 0–192 | 应设为 **192**（0dB） |
| 18 | DAC Notch Filter Switch | BOOL | |
| 19 | DAC Double Fs Switch | BOOL | |
| 21 | DAC Mono Mix Switch | BOOL | |
| 24 | **ADC Capture Volume** | INT 0–192 | 应设为 **192** |
| 25 | **ADC PGA Gain Volume** | INT 0–10 | 0=−3.5dB, 1=0dB, 2=+2.5, 3=+4.5, 4–7=+7~+16dB, 8–10=+18~+24dB。录音建议 **7（+16dB）** |
| 26 | ADC Soft Ramp Switch | BOOL | |
| 27 | ADC Double Fs Switch | BOOL | |
| 28 | ALC Capture Switch | BOOL | 自动电平控制，建议 **off** |
| 29/30/31 | ALC Capture Max/Min/Target Volume | INT | |
| 35 | ALC Capture Noise Gate Switch | BOOL | |
| 38 | **Headphone Jack** | CARD, BOOLEAN, **只读** | 耳机插入检测状态。插 3 段耳机实测 = `on` ✅ |
| 39 | **Headset Mic Jack** | CARD, 只读 | 耳麦麦克风插入检测 |
| 40 | `spk switch` | BOOL | 板级 GPIO 开关，**设置后会立刻弹回**，无效 |
| 41 | `hp switch` | BOOL | 板级 GPIO 开关，**设置后会立刻弹回**，无效 |
| 42 | **`Headphone Switch`** | BOOL | ✅ **真正的耳机输出开关**，可设置、可持久化 |
| 43 | **`Speaker Switch`** | BOOL | 板载喇叭开关，应 **off** |
| 44 | **`Main Mic Switch`** | BOOL | 主麦克风通路，默认 `on` |
| 45 | **`Headset Mic Switch`** | BOOL | 耳麦麦克风通路，默认 `off` |
| 46 | `Differential Mux` | ENUM | 可选 `... with 20db Boost` |
| 47 | `Digital Mic Mux` | ENUM | 默认 `dmic disable` |
| 48 | DAC SRC Mux | ENUM | |
| 49/50 | Left/Right Hp mux | ENUM | 默认 `lin1-rin1` |
| 51/53 | LLIN / RLIN Switch | BOOL | |
| 52/54 | Left / Right DAC Switch | BOOL | |

### 最坑的一对：`hp switch` vs `Headphone Switch`

```
numid=41  hp switch           ← 简化视图里的名字，板级 GPIO，设 on 后毫秒级弹回 off
numid=42  Headphone Switch    ← 真正的编解码器开关，可 set、可保持
```

**排查时我一度死磕 numid=41，方向完全错了。** 认准 **numid=42**。

---

## 5. 根因：UCM 配置与内核不匹配

镜像自带 `/usr/share/alsa/ucm2/conf.d/rockchip-es8316/HiFi.conf`：

```
SectionVerb {
  EnableSequence [
    cset "name='Left Headphone Mixer Left DAC Switch' on"    ← 本版内核不存在此控件
    cset "name='Right Headphone Mixer Right DAC Switch' on"  ← 本版内核不存在此控件
  ]
}

SectionDevice."Headphones" {
  EnableSequence [ ]      ← 空的，从未打开耳机输出
}
```

`cset` 找不到控件时**静默失败**，结果：

- UCM 的 BootSequence 部分失效
- **耳机输出通路在开机时从未被使能** → `Headphone Switch` 保持 `off`

这是 Radxa 上游打包问题，不是操作错误。（本版内核对应的是 `Left DAC Switch`(52) / `Right DAC Switch`(54)。）

---

## 6. 正确的初始化序列（可直接复制）

```bash
amixer -c rockchipes8316 cset iface=MIXER,name='Speaker Switch' off
amixer -c rockchipes8316 cset iface=MIXER,name='Headphone Switch' on
amixer -c rockchipes8316 cset iface=MIXER,name='Headphone Playback Volume' 3
amixer -c rockchipes8316 cset iface=MIXER,name='Headphone Mixer Volume' 11
amixer -c rockchipes8316 cset iface=MIXER,name='DAC Playback Volume' 192
```

录音侧（需要麦克风时）：

```bash
amixer -c rockchipes8316 cset iface=MIXER,name='ADC PGA Gain Volume' 7
amixer -c rockchipes8316 cset iface=MIXER,name='ADC Capture Volume' 192
amixer -c rockchipes8316 cset iface=MIXER,name='ALC Capture Switch' off
# 3 段耳机（无麦）→ Main Mic on / Headset Mic off
# 4 段耳麦        → Main Mic off / Headset Mic on
amixer -c rockchipes8316 cset iface=MIXER,name='Headset Mic Switch' on
```

---

## 7. 持久化：已解决，重启自动生效

机制：`alsa-restore.service`（`static` + `active`）在每次开机时恢复 `/var/lib/alsa/asound.state`。

```bash
sudo alsactl store      # 保存当前状态
```

**实测验证（2026-09-21）**：设置 → `alsactl store` → 重启 → 无任何手动操作：

```
ES8316 卡号             : 2
numid=42 Headphone Switch  : values=on        ✅ 保持
numid=43 Speaker Switch    : values=off       ✅
numid=15 DAC Playback Vol  : values=192,192   ✅
耳机自动放音                                   ✅ 人工确认听到
```

**结论：开机即有声音，程序里不需要写任何混音器初始化代码。**

---

## 8. PipeWire / WirePlumber 会接管混音器

- 默认 ALSA 输出 = PipeWire；PipeWire 默认 sink **就是 ES8316**（不是 HDMI，这点已实测确认）
- **WirePlumber 会把 PipeWire 的 sink 音量映射到硬件 `Headphone Playback Volume`**：
  - PipeWire `0.16` → 硬件档位 `0`
  - PipeWire `0.80` → 硬件档位 `3`
- 手动 `amixer` 改这个控件**在 PipeWire 不活动时能保持**，但会被 WirePlumber 的状态同步覆盖

**对项目的指导：程序应直连硬件设备，绕开 PipeWire：**

```python
# 用 sounddevice 时
sd.OutputStream(device="plughw:CARD=rockchipes8316,DEV=0", ...)
```

一举两得：
1. 输出音量完全由自己的代码控制，不受 PipeWire 状态影响
2. 绕开 PipeWire 的 graph quantum（默认 1024 帧 ≈ 21ms），降低延迟

---

## 9. 实测数据

### 9.1 输出

```bash
timeout 6 speaker-test -c 2 -t sine -f 440
```

→ 左右耳均能听到 440Hz ✅

> `speaker-test` 会请求**最大缓冲区**（`Using max buffer size 1048576` ≈ 21.8s @48kHz），
> 它输出的 `Time per period = 11.18` **不代表真实延迟**。真实延迟由自己指定 `blocksize` 决定（256 帧 ≈ 5ms）。

### 9.2 采集（无麦克风时的基线）

```bash
arecord -D plughw:CARD=rockchipes8316,DEV=0 -f S16_LE -r 48000 -c 2 -d 3 -t raw /tmp/mic.raw
od -An -td2 -v -w2 /tmp/mic.raw | sort | uniq -c | sort -rn | head
```

结果（288,000 样本）：

```
   9194  -32768     ← 3.2% 触负满量程
   3872      -9
   3822      -6
   3800     -12     ← 其余是 −1 ~ −15 的微小噪声底
```

**判读：这是"输入端悬空"的噪声底，不是数字静音。** 说明 **ADC → I2S → ALSA → arecord 整条采集链路正常**，缺的只是麦克风本体。

排查时可用此法判断"是没麦克风还是链路坏了"：全 0 = 链路问题；有噪声分布 = 链路正常。

---

## 10. 速查表

```bash
# 当前卡号（会漂移，仅供查看）
cat /proc/asound/cards

# 耳机是否插入（只读）
amixer -c rockchipes8316 cget numid=38

# 耳机开关状态
amixer -c rockchipes8316 cget numid=42

# 全部控件（全名）
amixer -c rockchipes8316 controls | grep iface=MIXER

# 播放测试
timeout 6 speaker-test -c 2 -t sine -f 440

# 录音测试
arecord -D plughw:CARD=rockchipes8316,DEV=0 -f S16_LE -r 48000 -c 2 -d 3 /tmp/mic.wav
aplay /tmp/mic.wav

# 保存混音器状态（重启后自动恢复）
sudo alsactl store

# PipeWire 侧
wpctl status
wpctl get-volume @DEFAULT_AUDIO_SINK@
wpctl set-volume @DEFAULT_AUDIO_SINK@ 0.8
```

---

## 11. 排查过程时间线（避免重走）

| 步骤 | 现象 | 结论 |
|---|---|---|
| 1 | `aplay -L` 默认是 PipeWire，下面挂 5 个同名 "Built-in Audio" | 担心默认输出跑到没接显示器的 HDMI |
| 2 | `wpctl inspect 50` | 确认默认 sink **就是 ES8316**，虚惊一场 |
| 3 | `amixer sget 'Playback Volume'` 报找不到 | 控件名猜错；`amixer scontrols` 只显示简化名 |
| 4 | 发现 `hp switch` = off、`Headphone` = 0% | 以为根因是这两个 |
| 5 | 插耳机后 `hp switch` 仍 off | 误判"插入检测失效"，准备写 systemd 服务 |
| 6 | 用 `amixer controls` 才看到全名 | **发现 UCM 的全名其实存在，`sget` 看不到** |
| 7 | `numid=38 Headphone Jack = on` | **插入检测完全正常**，之前的"失效"是误判 |
| 8 | `hp switch`(41) 设 on 立刻弹回；`Headphone Switch`(42) 能保持 | **认准 numid=42**，之前一直在跟错的控件较劲 |
| 9 | `alsactl store` + 重启 | ✅ 状态保持，耳机自动放音 |
| 10 | 录音测试看到噪声底而非全 0 | 采集链路正常，单纯没有麦克风 |

**三个最浪费时间的误区：**
1. 用卡号（`-c 4`）而不是卡名 → 重启后必然踩坑
2. 用 `sget`/`scontrols` 查全名控件 → 误判为"控件不存在"
3. 死磕 `hp switch`(41) 而不是 `Headphone Switch`(42) → 方向错误

---

## 12. 待办

- [ ] **修复 UCM**：在 `/etc/alsa/ucm2/` 下放一份覆盖配置（优先级高于 `/usr/share/alsa/ucm2/`），把 `Left/Right Headphone Mixer ... DAC Switch` 换成实际存在的 `Left/Right DAC Switch`，并给 `Headphones` 补上 `Headphone Switch on` 的 EnableSequence。**目前非必需**（`alsactl store` 已解决持久化），但能防止将来系统升级后再次失效
- [ ] 麦克风：等 USB 声卡到货，或用 4 段耳麦测试板载麦克风通路
- [ ] 实时延迟调优：确定 blocksize 与是否使用 `hw:` 设备
