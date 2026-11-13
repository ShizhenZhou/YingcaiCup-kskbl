# CosyVoice2 音色克隆 —— RK3588 部署与音色注册

> 记录日期：2026-10-17 ~ 2026-11-13
> 硬件：ROCK 5B+（RK3588，16 GB）
> 模型：[`Sariel00/cosyvoice2_rknn`](https://huggingface.co/Sariel00/cosyvoice2_rknn)（CosyVoice2-0.5B 的一步蒸馏 RKNN/RKLLM 版）
> 结论：**在板子上跑通了零样本音色克隆，并且自己写出了原仓库没有的音色注册工具**

---

## 0. 一句话结论

| 项 | 结果 |
|---|---|
| NPU 流水线 | ✅ 编译通过，端到端出音频 |
| 默认音色 | ✅ TTFT 1.9 s，持续 RTF **0.80**（比实时快） |
| **任意音色注册** | ✅ **自己实现**（原仓库只验证了固定音色） |
| 曼波音色克隆 | ✅ F0 320 Hz（参考素材 324 Hz） |
| 部署体积 | 2.9 GB（精简后，原始归档 12 GB） |
| 峰值内存 | 1.6 GB |

---

## 1. 为什么需要它 —— DSP 调参的硬上限

在此之前用的是自研的**相位声码器变声**（`src/kskbl/voice.py`）：

```
先时间拉伸 r 倍 → 再重采样 1/r → 音高和共振峰一起搬移
```

这套东西能做出「瓦瓦音」「花栗鼠」这类**夸张的变调效果**，但**复现不了某个特定人的音色**。

### 1.1 当时做过的尝试

拿曼波素材测出 F0 = 352.8 Hz，然后按比例变调，逐个试了 5 档共振峰：

| 参数 | 结果 |
|---|---|
| 音高 +18.86，共振峰 +0 | 太尖 |
| 音高 +18.86，共振峰 +6 | 偏成熟 |
| 音高 +18.86，共振峰 +12 | 中性 |
| 音高 +18.86，共振峰 +18.86 | 卡通 |
| 音高 +18.86，共振峰 +24 | 极尖 |

**用户反馈：「都不像，应该有特别的算法，不能按你这么调。」**

### 1.2 为什么调不出来

一个声音的「身份」不在基频和共振峰位置上，而在细节里：

| 决定「像谁」的因素 | DSP 能改吗 |
|---|---|
| 基频 | ✅ 能 |
| 共振峰的整体位置 | ✅ 能 |
| **共振峰的精细形状**（F1/F2/F3 的相对距离、带宽） | ❌ 不能（只能整体拉伸） |
| **气声比例** | ❌ 不能 |
| **音质细节**（紧张度、喉位、鼻腔共鸣） | ❌ 不能 |

**结论：信息量在模型参数里，不在两个旋钮里。**

---

## 2. 这个模型是什么

### 2.1 技术路线

不是调 DSP，而是用神经网络**从参考音频编码出音色特征**，再合成任意文本。

CosyVoice2 是**零样本（zero-shot）** 模型 —— 不需要训练，给一段参考音频就能克隆。

### 2.2 流水线结构

```
规范化文本
  → C++ GPT-2/BPE 文本前端
  → Qwen2 W8A8 RKLLM（NPU0+1）
  → Speech Head W8A8 RKNN（NPU0）
  → 一步缓存 Flow FP16 RKNN（NPU2）
  → HiFT FP16 RKNN（NPU2）
  → 24 kHz PCM 流
```

### 2.3 参数量实测

| 组件 | 参数量 | 精度 |
|---|---|---|
| Qwen2 LLM | 约 509 M | W8A8 |
| 嵌入表（text/speech/control） | 142.0 M | FP32 |
| 语音 tokenizer | 123.7 M | ONNX |
| Flow Estimator | ~86.9 M | FP16 |
| Flow Encoder | ~53.4 M | FP16 |
| HiFT Decoder | ~20.6 M | FP16 |
| CampPlus 说话人编码器 | 6.9 M | ONNX |
| HiFT F0 | ~3.3 M | FP16 |
| **合计** | **约 781 M** | — |

> 名字里的「0.5B」指 LLM 部分（Qwen2-0.5B ≈ 494 M），不含 vocoder / tokenizer。
>
> 单 `text_embedding` 一张表就占 **136 M**（词表 151,936 × 896），吃掉整个 LLM 的四分之一参数 —— 这是端侧 TTS 省不下来的地方。

---

## 3. 部署步骤

### 3.1 环境要求

| 依赖 | 版本 | 说明 |
|---|---|---|
| RKNN Runtime | 2.3.2 | 仓库自带 `librknnrt.so` |
| RKLLM Runtime | 1.3.0 | 仓库自带 `librkllmrt.so` |
| RKNPU 驱动 | 0.9.8 | 编进内核，`lsmod` 查不到属正常 |
| cmake / g++ / libpcre2-dev | — | **编译 C++ 引擎必需** |

```bash
sudo apt install -y build-essential cmake libpcre2-dev
```

> ⚠️ `libpcre2-dev` 在 CMakeLists 里是 `REQUIRED`，缺了直接配置失败。

### 3.2 精简下载（12 GB → 2.9 GB）

原始归档约 12 GB，但真正要跑只需要这些：

```
rknn/models/      RKLLM + RKNN 权重
rknn/cpp/         C++ 源码
rknn/runtime/     librknnrt.so / librkllmrt.so
rknn/scripts/     启动脚本
rknn/examples/    示例
rknn/benchmarks/  runtime_input 里的 prompt 参考数据
rknn/fixtures/    flow fixture（spks 等）
runtime/          默认音色、文本规范化资源
onnx/models/      campplus.onnx + speech_tokenizer（注册音色用）
onnx/fixtures/prompt/  校验用的黄金数据
```

**跳过**：`original/`（CosyVoice 源码）、`onnx/conversion/`、`rknn/conversion/`、`tests/results/`。

### 3.3 编译

```bash
cd ~/proj/cosyvoice2_rknn/rknn/cpp
mkdir -p build && cd build
cmake .. -DCMAKE_BUILD_TYPE=Release
make -j6 cosyvoice2_streaming_pipeline
```

产物：`rknn/cpp/build/cosyvoice2_streaming_pipeline`（369 KB）。

> CMakeLists 里有些目标依赖 ONNXRuntime / soxr，但主目标 `cosyvoice2_streaming_pipeline`
> 只需要 `rkllmrt + rknnrt + pcre2`，缺那些库不影响编译。

### 3.4 跑默认音色

```bash
cd ~/proj/cosyvoice2_rknn
R=$PWD/rknn
export NEXTTTS_ROOT=$R
export PIPELINE_BIN=$R/cpp/build/cosyvoice2_streaming_pipeline
export TEXT_FILE=/tmp/target.txt          # 要念的文本，UTF-8
export OUTPUT_ROOT=/tmp/cosy_out
bash $R/scripts/run_cosyvoice2_streaming_rk3588.sh sample
```

---

## 4. ⭐ 音色注册 —— 原仓库没做的部分

### 4.1 为什么必须自己写

原仓库只有一个 `onnx/conversion/source/top_level/capture_cosyvoice2_prompt_fixture.py`，
它的作用**不是给用户注册音色**，而是校验转换后的流水线能否复现冻结的基准：

```python
if not alignment["prompt_token_exact"]:
    raise RuntimeError("prompt speech tokens differ from the frozen flow fixture")
if alignment["embedding_max_abs"] > 1e-5:
    raise RuntimeError(f"CampPlus embedding mismatch: {alignment}")
```

**一旦结果和固定基准不一样就直接报错** —— 说明作者只用它跑过那一个固定音色。

README 的「已知限制 5」也写明：

> 固定音色已验证，**任意新参考 WAV 的纯板端注册尚未完成严格验收**。

所以 `tools/cosy_register.py` 是自己写的。

### 4.2 注册要产出什么

| 文件 | 内容 | 用途 |
|---|---|---|
| `prompt_text.txt` | 参考音频的转写文字 | 记录 |
| `prompt_text_token.i32.bin` | 转写文字的 BPE token | LLM 前缀 |
| `llm_prompt_speech_token.i32.bin` | 前 3 秒的语音 token（75 个） | LLM 前缀 |
| `flow_prompt_token_50.i32.bin` | 末 2 秒的语音 token（50 个） | Flow 条件 |
| `prompt_feat_100.f32.bin` | 末 2 秒的 24k mel（100 帧） | Flow 条件 |
| `spks.f32.bin` | 说话人向量（80 维，**已归一化**） | 音色 |

### 4.3 六步校验链（关键）

仓库自带的 `onnx/fixtures/prompt/` 提供了**每一步的黄金输出**，可以逐项对比：

| # | 环节 | 方法 | 校验结果 |
|---|---|---|---|
| ① | 重采样 44.1k → 16k/24k | `scipy.signal.resample_poly` | corr **0.9999** |
| ② | Kaldi fbank（80 维） | 手写：25ms 帧长 / 10ms 帧移 / Povey 窗 / 预加重 0.97 | embedding cos **0.9465** |
| ③ | Whisper log-mel（128 维） | 手写：slaney mel + log10 + 归一化 | corr **0.9998** |
| ④ | Speech tokenizer | ONNX（orrt） | **bit-exact** |
| ⑤ | 24k mel（80 维） | 手写：n_fft=1920 / hop=480 / **fmax=8000** | corr **0.9956** |
| ⑥ | BPE 文本分词 | 解析 `tokenizer.bin` + Qwen2 正则 + GPT-2 byte encoder | **bit-exact** |

**②的 0.9465 已经够用** —— 作者自己在 README 里写，模型生成的音频对参考音频的 CampPlus 余弦只有 **0.66~0.75**，我们的特征提取误差（0.9465）远小于模型固有的克隆损失。

### 4.4 用法

```bash
cd ~/proj/cosyvoice2_rknn
R=$PWD/rknn

~/proj/venv/bin/python $R/scripts/cosy_register.py \
  --wav /home/radxa/proj/manbo2.wav \
  --text-file /home/radxa/proj/voices/manbo_prompt_text.txt \
  --model-root $R/models/cosyvoice2 \
  --onnx-root $PWD/onnx \
  --out /home/radxa/proj/voices/manbo
```

**文字从哪来**：用项目自己的 SenseVoice 转写参考音频的前 3 秒。

```python
from kskbl.asr import Recognizer
rec = Recognizer(model_dir="/home/radxa/proj/models/sense_voice")
r = rec.recognize(wav16[:48000], 16000)   # 前 3 秒
```

实测对曼波素材得到：「我是曼波哈基米欧南北绿豆。这是。」—— 和文件名完全对得上。

---

## 5. 性能实测

| 场景 | 音频时长 | TTFT | 持续 RTF | 总耗时 |
|---|---|---|---|---|
| 第一次（默认音色） | 4.68 s | 1.90 s | 0.796 | 10.2 s |
| 曼波音色 | 4.20 s | 2.15 s | 0.808 | 9.7 s |
| 曼波音色（往返测试） | 6.00 s | — | 0.777 | 10.9 s |

**每轮约 3.7 秒是重新加载模型**（RKLLM 846 ms + 其余初始化）。做成常驻服务能压到 5~6 秒。

峰值内存 **1.6 GB**。

---

## 6. ⚠️ 踩过的四个坑

### 坑 1：24k mel 的 `fmax` 必须是 8000，不是 Nyquist

```python
# ❌ 错：默认取 sr/2 = 12000
mel = mel_spectrogram(..., fmax=None)

# ✅ 对：Matcha/CosyVoice 显式传 8000
mel = mel_spectrogram(..., fmax=8000)
```

**症状**：mel 相关性只有 0.96，克隆输出音色不对。
**修好后**：0.9600 → **0.9956**。

### 坑 2：speech tokenizer 是**固定 1753 帧**的静态模型

模型文件名 `speech_tokenizer_v2.static_seq1753.onnx` 已经说明了 —— 只接受 17.53 秒的输入。

- **不能补零**：补零会稀释 transformer 的注意力，实测 3 秒素材补零后 token 只对上 **37/75**
- **正确做法**：用满 17.53 秒的真实音频

曼波素材只有 31 秒，切一段 17.53 秒正好。

### 坑 3：`spks` 必须做 L2 归一化

```python
spks = w_affine @ embedding + b_affine
spks = spks / np.linalg.norm(spks)      # ← 少这一步就全废
```

**不归一化的后果**：

| | 范围 | 范数 |
|---|---|---|
| 参考 `spks` | ±0.31 | 0.82 |
| 未归一化 | ±6.19 | 14.65 |

**症状**：flow 输出被压成**近乎静音**（RMS −43.7 dBFS，正常应该 −18.9）。听起来就是「一片沙沙声」。

### 坑 4：flow prompt 必须从**真实语音**取，不能取补零后的尾部

素材短于 17.53 秒时要补零给 tokenizer 用。如果直接取「末 2 秒」当 flow prompt：

```
10.2 秒素材 + 7.3 秒补零
→ 末 2 秒 = 纯静音
→ flow 没有音色参考
→ 输出噪声
```

**实测对比**：

| | F0 | 谱平坦度 | 结论 |
|---|---|---|---|
| 修复前 | 274 Hz（≈默认音色 261） | 偏高 | 噪声 ❌ |
| 修复后 | 320 Hz（参考素材 324） | 0.010 | 正常语音 ✅ |

**修法**：mel 用未补零的原始音频算，flow token 取真实语音时长对应的前 N 个。

---

## 7. 已知限制

| 限制 | 说明 |
|---|---|
| **不是语音→语音** | 这是文字→语音。语调/节奏会因中间过了一道文字而丢失 |
| 首次调用慢 | 每轮 3.7 s 加载模型，做常驻服务可优化 |
| 克隆保真度有限 | 作者实测生成音频对参考的 CampPlus 余弦 0.66~0.75 |
| 一步蒸馏有失真 | README 承认听感低于官方 10 步模型 |
| 长文本要切句 | 建议 25 秒以上按语义切分 |

---

## 8. 命令速查

```bash
# 注册音色
cd ~/proj/cosyvoice2_rknn && R=$PWD/rknn
~/proj/venv/bin/python $R/scripts/cosy_register.py \
  --wav <参考音频> --text-file <转写文字> \
  --model-root $R/models/cosyvoice2 --onnx-root $PWD/onnx \
  --out ~/proj/voices/<音色名>

# 合成
export NEXTTTS_ROOT=$R
export PIPELINE_BIN=$R/cpp/build/cosyvoice2_streaming_pipeline
export VOICE_PROFILE_ROOT=~/proj/voices/<音色名>/voice_profile
export RUNTIME_ROOT=~/proj/voices/<音色名>/runtime_input
export FLOW_FIXTURE=~/proj/voices/<音色名>/flow_fixture
export TEXT_FILE=/tmp/target.txt
export OUTPUT_ROOT=/tmp/cosy_out
bash $R/scripts/run_cosyvoice2_streaming_rk3588.sh sample
```

主程序里通过 `src/kskbl/cosy.py` 调用，更简单：

```python
from kskbl.cosy import CosyVoice

cv = CosyVoice.from_preset("manbo")     # 读 ~/proj/voices/manbo/
wav, metrics = cv.synthesize("今天天气真好", on_progress=print)
```

---

## 9. 三条技术路线对比（答辩用）

| 方式 | 参数量 | 耗时（6 秒音频） | 保留语调 | 像特定人 |
|---|---|---|---|---|
| **DSP 相位声码器** | **0** | **0.4 s** | ✅ 保留 | ❌ 只是整体升调 |
| **SenseVoice + CosyVoice2** | 234 M + 781 M | 8~20 s | ❌ 丢失 | ✅ 像曼波 |
| （参考）RVC / so-vits-svc | 更大 | 更慢 | ✅ 保留 | ✅ 但需要训练 |

**两条能用的路各有取舍**，演示时对比着讲很有说服力。
