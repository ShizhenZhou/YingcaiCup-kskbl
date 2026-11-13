# AI 语音链路 —— 可行性验证记录

> 记录日期：2026-09-23
> 硬件：ROCK 5B+（RK3588，16 GB）
> 结论：**语音识别 + 语音合成两块都在板子上跑通了，性能有大量余量**

---

## 0. 一句话结论

| 功能 | 方案 | 实测速度 | 可行性 |
|---|---|---|---|
| **语音识别 ASR** | SenseVoice int8（sherpa-onnx） | **20~24 倍速**（CPU） | ✅ **完全可行** |
| **语音合成 TTS** | MeloTTS-RKNN2（RKNN **NPU**） | **4.5~4.7 倍速** | ✅ **完全可行** |
| 中英双语 | 都支持；ASR 还能**自动判语种** | — | ✅ |
| 采样率转换 | ASR 内部**自动** 48k/44.1k → 16k | — | ✅ 不用自己写 |
| TTS 响度 | 原始输出偏小，需归一化 +17 dB | — | ⚠️ 已在模块里处理 |

**整条 AI 链路在板子上完全跑得动，可以进入系统集成阶段。**

---

## 1. 语音识别（ASR）

### 1.1 选型

| 候选 | 大小 | 特点 | 选择 |
|---|---|---|---|
| **SenseVoice** | 228 MB（int8） | 中英日韩粤，**自动判语种**，非流式 | ✅ **采用** |
| streaming-zipformer | ~320 MB | 边说边出字，准确率略低 | ❌ 暂不用（流式字幕先不做） |

选 SenseVoice 的理由：非流式准确率更高、语种自动判别、体积可接受。

### 1.2 实测数据

用模型自带的测试音频：

| 音频 | 时长 | 识别耗时 | 实时倍率 | 语种 | 结果 |
|---|---|---|---|---|---|
| `test_wavs/zh.wav` | 5.59 s | 0.265 s | **21.1×** | `<\|zh\|>` ✅ | 开饭时间早上9点至下午5点。 |
| `test_wavs/en.wav` | 7.15 s | 0.298 s | **24.0×** | `<\|en\|>` ✅ | The tribal chieftain called for the boy and presented him with 50 pieces of code. |

真实场景：

| 场景 | 音频 | 识别耗时 | 倍率 | 结果 |
|---|---|---|---|---|
| TTS 合成 → 再识别（回环） | 7.82 s @44.1k | 0.394 s | 19.9× | 未听会变声的智能终端，这是我们的校赛作品。现在做一(次)掌文本跟语音合成测试。 |
| 麦克风实录 | 6.00 s @48k | 0.259 s | 23.2× | 你的新版，如果如果要做板子，可以做个那个插座那种，就是有一个就是。 |

> TTS→ASR 回环：期望「会听会变声…长文本的语音合成测试」，识别出 3 处字错（会→未、长→掌、的→跟），
> 约 **90% 字符准确率**。考虑到是合成语音再识别的双重损耗，这个结果合格。

### 1.3 ⭐ 关键发现：自动重采样

SenseVoice 内部是 **16 kHz**，但我们录音是 48 kHz。**不用自己重采样** —— sherpa-onnx 会处理：

```
/project/sherpa-onnx/csrc/offline-stream.cc: AcceptWaveformImpl
  Creating a resampler: in_sample_rate: 48000 → output_sample_rate: 16000
```

直接把原始采样率和数据丢进 `accept_waveform(sample_rate, samples)` 即可。

### 1.4 附加能力（值得利用）

- **语种自动判别**：`result.lang` 返回 `<|zh|>` / `<|en|>` / `<|ja|>` / `<|ko|>` / `<|yue|>`
- **情感识别**：`result.emotion`（SenseVoice 特有）
- **事件识别**：`result.event`（笑声、音乐等）
- **ITN**（逆文本正则化）：`use_itn=True` 会把「九点」转成「9点」

> 💡 **情感/事件识别可以直接驱动屏幕和灯效** —— 评委对着麦克风笑一下，屏幕就有反应，
> 这是现成的加分点，不用自己训练模型。

---

## 2. 语音合成（TTS）

### 2.1 选型

**MeloTTS-RKNN2**（HuggingFace: `happyme531/MeloTTS-RKNN2`）

- 作者直接在 RK3588 上优化过，**解码器跑 NPU**
- 中英混排（底层是多语种 MeloTTS）
- 实测内存约 0.2 GB

### 2.2 模型结构

```
encoder.onnx    30 MB   ← 跑 ONNX Runtime（CPU）。作者说编码器没上 NPU 加速
decoder.rknn    78 MB   ← 跑 RKNN（NPU），主要算力在这
decoder.onnx   134 MB   ← 备用（纯 CPU 版）
g.bin          1 KB     ← 静态输入
lexicon.txt / tokens.txt
```

### 2.3 实测数据

| 文本 | 编码器 | 解码器 | 推理合计 | 生成音频 | 实时倍率 |
|---|---|---|---|---|---|
| 短句（12 字） | 31.7 ms | 3 切片 × ~200 ms = 601 ms | 633 ms | 2.70 s | **4.3×** |
| 长句（36 字，两句） | 79 ms | 8 切片 × ~200 ms = 1600 ms | 1.68 s | 7.82 s | **4.65×** |

一次性开销：**模型加载 2.5 s + RKNN 初始化约 3 s**，所以必须做成常驻对象。

### 2.4 ⚠️ 响度问题（必须处理）

模型原始输出**电平偏低**：

```
原始输出:  峰值 -18.0 ~ -20.0 dBFS，RMS -32.2 dBFS   ← 听着明显偏小
归一化后:  峰值  -1.0 dBFS，RMS -15.2 dBFS           ← 加 +17 dB
```

即使把扬声器开到最大（37/37）也偏小。**解决办法是在合成后做峰值归一化**，
已经封在 `Synthesizer.normalize()` 里，`say()` 默认就会做。

### 2.5 ⚠️ 许可证

MeloTTS-RKNN2 是 **AGPL-3.0**。

我们的做法：**只把它当作外部模块 import，不把它的源码复制进 kskbl 仓库**。
`src/kskbl/tts.py` 里通过 `sys.path.insert(model_dir)` + `import melotts_rknn` 调用它，
许可证边界最清楚。

> 如果以后要打包发布，需要单独评估 AGPL 义务。

---

## 3. 环境搭建（含全部踩过的坑）

### 3.1 最终环境清单

| 组件 | 版本 | 说明 |
|---|---|---|
| Python | 3.11.2 | aarch64 |
| sherpa-onnx | **1.13.8** | venv；有官方 `manylinux2014_aarch64` 轮子 |
| onnxruntime | 1.31.0 | venv；TTS 编码器用 |
| numpy | 1.26.4 | venv（系统是 1.24.2，venv 里单独装的） |
| soundfile | 0.14.0 | venv；读写 WAV |
| cn2an / pypinyin / jieba | — | venv；中文文本前端 |
| huggingface_hub | — | venv；下载模型用 |
| **rknnlite** | **2.3.0** | **系统包** `python3-rknnlite2` |
| librknnrt.so | 2.3.0 | 系统 |
| NPU 驱动 | 编进内核 | `devfreq` 显示 1 GHz，`status=okay` |

### 3.2 坑 ①：venv 看不到系统包

`rknnlite` / `spidev` / `gpiod` / `PIL` 都是系统 apt 包，venv 默认看不到。

**不用重建 venv**，加一个 `.pth` 文件即可（等价于 `--system-site-packages`）：

```bash
echo /usr/lib/python3/dist-packages \
  > ~/proj/venv/lib/python3.11/site-packages/system-packages.pth
```

### 3.3 坑 ②：HuggingFace 的 Xet 后端报 401

用 `hf-mirror.com` 下载大文件时：

```
RuntimeError: CAS Client Error: HTTP status 401 Unauthorized
domain: https://cas-server.xethub.hf.co/v2/reconstructions/...
```

**原因**：HuggingFace 新的 Xet 存储后端，hf-mirror 没有代理它的 CAS 服务器。

**解决**：禁用 Xet，强制走传统 HTTP 下载：

```python
os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"
os.environ["HF_HUB_DISABLE_XET"] = "1"      # ← 关键
```

禁用后实测：**271 MB / 12.8 秒**、**228 MB / 12.8 秒** —— 速度很好。

### 3.4 坑 ③：GitHub Releases 从板子下不动

`sherpa-onnx` 的模型默认放在 GitHub Releases。板子上实测：

```
1 KB 范围请求  → 成功（命中边缘缓存）
20 MB 范围请求 → 90 秒超时，文件都没生成
```

**结论：GitHub Releases 大文件在校园网下不可用。**

**解决**：改用 HuggingFace 镜像（`hf-mirror.com`），仓库是 `csukuangfj/sherpa-onnx-<模型名>`。
而且 HF 上通常有 **int8 量化版**，比 Releases 里的完整 tarball 小得多：

```
GitHub 的 tarball      1.05 GB
HF 的 model.int8.onnx   228 MB   ← 只下这一个就够
```

### 3.5 坑 ④：不要 git clone HF 仓库

板子上**没装 git-lfs**，直接 `git clone` 只会拿到几十字节的 LFS 指针文件。

用 `huggingface_hub.snapshot_download` 或 `hf_hub_download`，它会正确处理 LFS。

---

## 4. 模块 API

### 4.1 `src/kskbl/asr.py`

```python
from kskbl.asr import Recognizer

rec = Recognizer()                    # 加载一次，约 2.8 秒
r = rec.recognize_file("/path/a.wav") # 或 recognize(samples, 48000)

print(r.text)                # 识别文本
print(r.lang)                # <|zh|> / <|en|>
print(r.emotion, r.event)    # 情感 / 事件
print(r.realtime_factor)     # 实时倍率

rec.close()
```

自检：`python src/kskbl/asr.py`

### 4.2 `src/kskbl/tts.py`

```python
from kskbl.tts import Synthesizer

tts = Synthesizer()                   # 加载一次，约 6 秒。**不要反复构造**
info = tts.say("你好，世界", path="/tmp/a.wav", play=True)
print(info["duration"], info["synth_elapsed"])

# 或者分步
audio = tts.synthesize("你好")
audio = tts.normalize(audio)          # -1 dBFS
tts.save("/tmp/b.wav", audio)

tts.close()
```

自检：`python src/kskbl/tts.py --out /tmp/x.wav --play`

---

## 5. 待办（2026-09-23 时）

- [x] 把 ASR + TTS 接进主程序框架（状态机：待机 → 录音 → 识别 → 处理 → 合成 → 播放）
- [x] 录音侧加**噪声门 + 高通滤波**（麦克风底噪问题，见音频子系统笔记）
- [ ] 探索用 `emotion` / `event` 驱动屏幕和灯效
- [ ] 评估是否需要流式识别（边说边出字幕）
- [x] 语音变声（DSP）—— 这一块还是空白，是本项目真正"原创"的部分

> ✅ = 后续已完成，详见第 6 节。

---

# 6. 【2026-11-13 更新】从「两块模型能跑」到「完整可演示系统」

> 1~5 节是 2026-09-23 的验证记录，**原文保留**。
> 本节汇总之后两个多月的实际进展。

---

## 6.1 系统现在长什么样

```
上电 → systemd 自动起服务 → 屏幕亮 → 「请插上声卡」
  ↓ 插上 USB 声卡
🔔 短嘀 → 立即录音（音量条）
  ↓ 说话…
  ↓ 拔掉声卡（或 3 秒静音）
录音结束 → 裁掉首尾静音
  ↓ SenseVoice 识别（17~20 倍速）
┌─ 命中「曼波关机/瓦瓦关机」？ → 断电
├─ 命中切换指令？            → 换音色
└─ 普通句子
    ↓ 屏幕显示识别文字
    ├─ 瓦瓦音色 → DSP 波形变声（0.4 秒）
    └─ 曼波音色 → CosyVoice 音色克隆（8~20 秒，带进度条）
  ↓ 插回声卡
🔊 播放 → 拔下 → 回待机
```

---

## 6.2 ⭐ 三条技术路线（答辩重点）

本项目最终**同时具备**两条变声路线，加上一条参考路线：

| 路线 | 参数量 | 耗时（6 秒音频） | 保留语调 | 像特定人 | 实现 |
|---|---|---|---|---|---|
| **① DSP 相位声码器** | **0** | **0.4 s** | ✅ 保留 | ❌ 只是整体升调 | **自己写的** |
| **② SenseVoice + CosyVoice2** | 234 M + 781 M | 8~20 s | ❌ 丢失 | ✅ 像曼波 | 部署 + **自研注册工具** |
| ③ RVC / so-vits-svc（未采用） | 更大 | 更慢 | ✅ | ✅ | 需训练目标音色模型 |

### 为什么最后两条都保留了

**因为它们的取舍刚好互补，演示时对比着讲很有说服力。**

### ② 的来由：「调参数永远调不像」这个结论

最开始只做 ①。拿曼波素材测出 F0 = 352.8 Hz，然后按比例变调，逐个试了 5 档共振峰：

| 音高 | 共振峰 | 听感 |
|---|---|---|
| +18.86 | +0 | 太尖 |
| +18.86 | +6 | 偏成熟 |
| +18.86 | +12 | 中性 |
| +18.86 | +18.86 | 卡通 |
| +18.86 | +24 | 极尖 |

**用户反馈：「都不像，应该有特别的算法，不能按你这么调。」**

原因在于 ① 只能改**基频**和**共振峰的整体位置**，改不了这些：

| 决定「像谁」的因素 | DSP 能改吗 |
|---|---|
| 共振峰的精细形状（F1/F2/F3 相对距离、带宽） | ❌ |
| 气声比例 | ❌ |
| 音质细节（紧张度、喉位、鼻腔共鸣） | ❌ |
| 语调模式（连读、重音、语气） | ❌ |

**信息量在模型参数里，不在两个旋钮里。** 于是才有第 ② 条路线。

> ② 的完整部署与踩坑记录见 **`docs/CosyVoice2_音色克隆部署.md`**

---

## 6.3 ① DSP 变声：自己实现的相位声码器

`src/kskbl/voice.py`，**纯 numpy，零参数，零训练**。

### 核心思路

```
① 相位声码器把音频拉长 r 倍   → 时长×r，音高不变，共振峰不变
② 再重采样压缩 1/r 倍        → 时长恢复，音高×r，共振峰也×r
净效果：时长不变，音高和共振峰同时上移 = 听起来像"更小的声道"
```

**一次操作同时搬移基频和共振峰** —— 不用单独做共振峰处理就能听出性别/音色变化。

### 关键实现细节

| 环节 | 要点 |
|---|---|
| 相位推进 | 必须按**实际跳过的 hop 数**算（`step`），不是固定 1 个 |
| 帧数 | `n_out = n_frames × rate`（写反会变成压缩） |
| 共振峰独立控制 | 倒谱包络搬移（`formant_shift`），纯音验证 F0 恒定 |

### 实测精度

| 半音 | 期望 | 实测 | 误差 |
|---|---|---|---|
| +3 | 523.25 Hz | 523.50 | +0.05% |
| +6 | 622.25 Hz | 622.50 | +0.04% |
| +12 | 880.00 Hz | 880.00 | 0.00% |
| −6 | 311.13 Hz | 311.00 | −0.04% |

**8 个档位误差全部 < 0.05%，长度不变，无静音填充。**

### 预设

| 名称 | 半音 | 实测 F0 |
|---|---|---|
| natural | +8.00 | 183.6 Hz |
| cartoon | +10.00 | 228.6 Hz |
| **wawa** | **+13.24** | **256.7 Hz** |
| wawa_oct | +25.24 | 484.8 Hz |
| manbo（DSP 版） | +18.86 | 353 Hz |

### 两个真实 bug（自己 debug 出来的）

| Bug | 现象 | 根因 |
|---|---|---|
| 相位推进 | +12 半音 → 1010 Hz（应 880） | 按 1 个 hop 算，实际跳了 `rate` 个 |
| 帧数方向 | 音频被压扁 4 倍速，长度测试却通过 | `n_out = n_frames / rate` 写反，被补零掩盖 |

---

## 6.4 语音指令

| 指令 | 说法 | 效果 |
|---|---|---|
| 切换音色 | 「我是曼波」/「切换到音色1」 | → 曼波音色 |
| 切换音色 | 「我是瓦瓦」/「切换到音色2」 | → 瓦瓦音色 |
| 关机 | 「曼波关机」/「瓦瓦关机」 | ⚡ 断电 |

**难点是 ASR 对固定词组识别极不稳定**（曼波→慢播/漫播/漫步，瓦瓦→娃娃/哇哇），
用**同音字字符类正则**解决：

```python
("manbo", re.compile("[曼慢漫满蛮蔓][波播拨博簸步]")),
("wawa",  re.compile("[瓦娃哇挖袜][瓦娃娃挖袜]")),
```

> 完整规则、幂等语义、测试用例见 **`docs/语音交互指令.md`**

---

## 6.5 部署形态

```bash
sudo bash deploy/apply.sh          # 一键部署 + 重启 + 校验版本
```

- systemd 服务，开机自启、崩溃自愈
- 免密关机规则（只授权 `poweroff` 两个命令，不给完整 root）
- 启动横幅打印代码修改时间，用于确认跑的是不是最新代码

> 详见 **`docs/部署与开机自启.md`**

---

## 6.6 参数量汇总

| 模型 | 用途 | 参数量 | 量化 |
|---|---|---|---|
| SenseVoice | 语音识别 | 234 M | int8 |
| MeloTTS | 语音合成（备用） | 约 43 M | FP32/FP16 |
| CosyVoice2 | 音色克隆 | 约 781 M | W8A8 / FP16 |
| **DSP 变声** | 变调 | **0** | — |

---

## 6.7 还没做的

- [ ] 用 `emotion` / `event` 驱动屏幕和灯效
- [ ] 流式识别（边说边出字幕）
- [ ] CosyVoice 常驻服务（把每轮 3.7 秒的模型加载省掉）
- [ ] 软件降噪（谱减 / 维纳滤波），提升嘈杂环境下的识别率
- [ ] 双门限（滞回）VAD：起判定用高门限、维持用低门限
- [ ] 瓦瓦变声响度补偿（目前比原声低约 5 dB）

