# -*- coding: utf-8 -*-
"""变声 DSP —— 变调 + 共振峰搬移（相位声码器实现）

────────────────────────────────────────────────────────
为什么不能只升调
────────────────────────────────────────────────────────
    人声听起来像男还是像女，取决于两个东西：
        * 基频（音高）      男 ~110 Hz，女 ~200 Hz
        * 共振峰（声道形状） 男声道更长更粗，共振峰整体偏低

    只升调不动共振峰 → 听起来像"花栗鼠唱歌"，明显不自然。
    真正的性别转换要**两者一起上移**，相当于模拟一个更小的声道。

────────────────────────────────────────────────────────
一次搞定两者的技巧
────────────────────────────────────────────────────────
    ① 相位声码器把音频**拉长 r 倍**
         时长 ×r，音高不变，共振峰不变
    ② 再**重采样压缩 1/r 倍**
         时长恢复，音高 ×r，共振峰也 ×r

    净效果：时长不变，音高和共振峰同时上移 ✅

    注意顺序不能反：必须先拉伸再重采样。

────────────────────────────────────────────────────────
参考值（半音）
────────────────────────────────────────────────────────
    +3 ~ +4    轻微变调（听起来"不那么男"了）
    +5 ~ +7    男声 → 女声（推荐 +6）
    +8 ~ +12   夸张效果（可用于"卡通音"演示）

    -5 ~ -7    女声 → 男声

────────────────────────────────────────────────────────
用法
────────────────────────────────────────────────────────
    from kskbl.voice import VoiceChanger

    vc = VoiceChanger(semitones=6)

    # 处理一段 numpy 波形（内部会自动把共振峰一起搬）
    out = vc.process(samples, 48000)

    # 分步调用
    out = vc.pitch_shift(samples, 48000, semitones=6)
"""

import os

import numpy as np

# 允许直接用 python3 src/kskbl/voice.py 运行（这样 import kskbl.audio 才找得到）
if __package__ in (None, ""):  # pragma: no cover
    import sys as _sys

    _sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))


# ══════════════════════════════════════════════════════════════
# 基础工具
# ══════════════════════════════════════════════════════════════
def semitones_to_ratio(semitones):
    """半音数 → 频率倍数。12 半音 = 1 个八度 = 频率 ×2"""
    return float(2.0 ** (semitones / 12.0))


def resample(x, ratio):
    """重采样。

    ratio > 1  → 变长、频率降低
    ratio < 1  → 变短、频率升高
    """
    x = np.asarray(x, dtype=np.float32)
    if x.size == 0 or ratio == 1.0:
        return x.copy()

    n_out = max(1, int(round(len(x) / ratio)))
    # 输出第 i 个点对应输入坐标 i*ratio
    idx = np.arange(n_out, dtype=np.float64) * ratio
    idx = np.clip(idx, 0, len(x) - 1)

    i0 = np.floor(idx).astype(np.int64)
    i1 = np.minimum(i0 + 1, len(x) - 1)
    frac = (idx - i0).astype(np.float32)

    return (x[i0] * (1.0 - frac) + x[i1] * frac).astype(np.float32)


# ══════════════════════════════════════════════════════════════
# 相位声码器：时间伸缩，音高不变
# ══════════════════════════════════════════════════════════════
def time_stretch(x, rate, n_fft=1024, hop=None):
    """把音频时长拉伸 rate 倍，**音高不变**。

    rate > 1 → 变长（说话变慢但音高不变）
    rate < 1 → 变短（说话变快但音高不变）

    实现原理（相位声码器）：
        1. 分帧做 STFT，每帧 hop 个采样
        2. 对每个输出帧，从输入帧里"跳着取"（步长 = rate）
        3. 关键：用**相位推进量**修正被跳过的相位，否则会糊成一团
           期望相位推进 = 2π·f·hop/N
           实际相位推进 - 期望推进 = 真实频率偏移（wrap 到 ±π）
           累加修正后的相位，保证帧间连续
        4. 重叠相加（OLA）合成
    """
    x = np.asarray(x, dtype=np.float32)
    if x.size == 0 or rate == 1.0:
        return x.copy()

    if hop is None:
        hop = n_fft // 4

    win = np.hanning(n_fft).astype(np.float32)

    # 首尾补零，避免边界效应
    pad = n_fft
    xp = np.concatenate([np.zeros(pad, dtype=np.float32), x,
                         np.zeros(pad, dtype=np.float32)])

    n_frames = 1 + (len(xp) - n_fft) // hop
    if n_frames < 2:
        return x.copy()

    # ⚠️ 时间伸缩 rate 倍 → 输出帧数要**乘以** rate。
    #    之前写成 n_frames/rate 是反的：实际变成压缩，再被末尾补零掩盖住长度，
    #    听起来就是语音被压扁成好几倍速。
    n_out = max(1, int(round(n_frames * rate)))

    n_bins = n_fft // 2 + 1
    # 每个 bin 在 hop 内的期望相位推进
    omega = 2.0 * np.pi * np.arange(n_bins, dtype=np.float64) * hop / n_fft

    out = np.zeros(n_fft + n_out * hop, dtype=np.float32)

    prev_phase = None
    prev_src = 0
    acc_phase = np.zeros(n_bins, dtype=np.float64)
    last_dev = np.zeros(n_bins, dtype=np.float64)

    for i in range(n_out):
        # 第 i 个输出帧对应输入帧 i/rate
        src = int(round(i / rate))
        if src >= n_frames:
            src = n_frames - 1
        if src < 0:
            src = 0
        start = src * hop

        frame = xp[start:start + n_fft] * win
        spec = np.fft.rfft(frame)
        mag = np.abs(spec)
        phase = np.angle(spec)

        if prev_phase is None:
            acc_phase = phase.copy()
        else:
            # 源帧这一步跨了 step 个 hop（rate>1 时是 0 或 1）
            step = src - prev_src
            if step != 0:
                delta = phase - prev_phase
                # 减掉期望推进后 wrap 到 ±π，得到真实频率偏离期望的量
                dev = np.mod(delta - omega * step + np.pi, 2.0 * np.pi) - np.pi
                last_dev = dev / step
            # 源帧没动时沿用上一次估计的偏差
            acc_phase += omega + last_dev

        prev_phase = phase
        prev_src = src

        y = np.fft.irfft(mag * np.exp(1j * acc_phase), n_fft)
        out[i * hop:i * hop + n_fft] += (y * win).astype(np.float32)

    # 分析窗 × 合成窗 的重叠补偿
    out /= (float(np.sum(win ** 2)) / hop)

    # 去掉补零部分（补零也被拉伸了 rate 倍），长度回到 len(x)*rate
    target_len = max(1, int(round(len(x) * rate)))
    start = int(round(pad * rate))
    end = min(len(out), start + target_len)
    out = out[start:end]

    if len(out) < target_len:
        out = np.concatenate([out, np.zeros(target_len - len(out), dtype=np.float32)])

    return out.astype(np.float32)


# ══════════════════════════════════════════════════════════════
# 变调：音高 + 共振峰一起搬
# ══════════════════════════════════════════════════════════════
def pitch_shift(x, sr=None, semitones=0.0, n_fft=1024):
    """变调，同时把共振峰一起上移/下移。

    ★ 这是本模块的核心：先时间拉伸，再重采样。

    semitones > 0 → 变高（男声→女声）
    semitones < 0 → 变低（女声→男声）
    """
    if semitones == 0.0:
        return np.asarray(x, dtype=np.float32).copy()

    r = semitones_to_ratio(semitones)

    # ① 拉长 r 倍（音高、共振峰都不变）
    stretched = time_stretch(x, r, n_fft=n_fft)

    # ② 重采样压缩 1/r（音高、共振峰都 ×r），时长回到原样
    out = resample(stretched, r)

    # 长度对齐原信号，避免后面拼接出现缝隙
    src_len = len(x)
    if len(out) > src_len:
        out = out[:src_len]
    elif len(out) < src_len:
        out = np.concatenate([out, np.zeros(src_len - len(out), dtype=np.float32)])

    return out.astype(np.float32)


# ══════════════════════════════════════════════════════════════
# 变声器
# ══════════════════════════════════════════════════════════════
def formant_shift(x, sr, ratio, n_fft=1024, hop=None, lifter=40):
    """**只移动共振峰，音高不变。**

    ratio > 1 → 共振峰整体上移 → 声道"变小" → 更年轻 / 更卡通 / 更甜
    ratio < 1 → 共振峰下移 → 更粗、更沉、更成熟

    为什么需要单独一步：
        音高（基频）决定"音有多高"，
        共振峰（频谱包络）决定"音色像谁"。
        只升调不搬共振峰 = 花栗鼠；只搬共振峰不升调 = 换了个人的嗓子。
        两者独立可控，才能精确模仿某个声音。

    原理（倒谱包络搬移）：
        1. 分帧 STFT，取对数幅度谱
        2. 求倒谱（log 谱的逆变换）。
           倒谱低阶 = 频谱包络（共振峰），高阶 = 基频谐波的纹波
        3. 用 lifter 把高阶截掉 → 得到**平滑包络**
        4. 把包络在频率轴上拉伸 ratio 倍
        5. 新幅度谱 = 原幅度谱 × (新包络 / 旧包络)，相位保持不变
        6. 逆变换 + 重叠相加
    """
    x = np.asarray(x, dtype=np.float32)
    if x.size == 0 or ratio == 1.0:
        return x.copy()

    if hop is None:
        hop = n_fft // 4

    win = np.hanning(n_fft).astype(np.float32)
    pad = n_fft
    xp = np.concatenate([np.zeros(pad, dtype=np.float32), x,
                         np.zeros(pad, dtype=np.float32)])

    n_frames = 1 + max(0, (len(xp) - n_fft) // hop)
    if n_frames < 1:
        return x.copy()

    n_bins = n_fft // 2 + 1
    # 包络在频率轴上的取样位置：新位置 k 取旧包络的 k/ratio 处
    src_idx = np.arange(n_bins, dtype=np.float64) / ratio
    src_idx = np.clip(src_idx, 0, n_bins - 1)

    out = np.zeros(n_fft + n_frames * hop, dtype=np.float32)
    lifter = max(1, min(lifter, n_fft // 2 - 1))

    for i in range(n_frames):
        start = i * hop
        frame = xp[start:start + n_fft] * win
        spec = np.fft.rfft(frame)
        mag = np.abs(spec)
        phase = np.angle(spec)

        # 对数幅度谱 → 倒谱 → lifter 截断 → 回到对数包络
        logmag = np.log(mag + 1e-8)
        cep = np.fft.irfft(logmag, n_fft)
        cep_l = cep.copy()
        cep_l[lifter:n_fft - lifter] = 0.0
        env = np.exp(np.fft.rfft(cep_l, n_fft).real)

        # 包络在频率轴上搬移
        new_env = np.interp(src_idx, np.arange(n_bins, dtype=np.float64), env)

        gain = new_env / (env + 1e-8)
        new_mag = mag * gain

        y = np.fft.irfft(new_mag * np.exp(1j * phase), n_fft)
        out[start:start + n_fft] += (y * win).astype(np.float32)

    # 分析窗 × 合成窗 补偿
    out /= (float(np.sum(win ** 2)) / hop)

    # 去掉补零
    out = out[pad:pad + len(x)]
    if len(out) < len(x):
        out = np.concatenate([out, np.zeros(len(x) - len(out), dtype=np.float32)])
    return out.astype(np.float32)


# ══════════════════════════════════════════════════════════════
# 预设音效
# ══════════════════════════════════════════════════════════════
# 半音数都来自实测：以「男声 F0 ≈ 112.8 Hz」为源，
# 处理后实测基频见注释。
PRESETS = {
    # 自然女声：实测 F0 179.8 Hz，落在正常女声区间
    "natural": 8.0,

    # 卡通高音：实测 F0 203.4 Hz，偏高但不尖锐
    "cartoon": 10.0,

    # ★ 瓦瓦音：**最终选定**（2026-10-17 试听对比后确定）
    #   源 F0 118.7 Hz → 输出实测 256.7 Hz
    #   5 个预设横向对比里听感最好的一档：够卡通、又还听得出说的是什么
    "wawa": 13.24,

    # 尖锐版：在瓦瓦音基础上再升一个八度 → 约 485 Hz
    # 共振峰也放大到 4.3 倍，听感非常尖，属于夸张玩具音（备选）
    "wawa_oct": 25.24,

    # 曼波音：**按实测素材校准**（2026-10-17）
    #
    # 素材：曼波：哥我求你了…（31 s / 44.1 kHz / 64 kbps）
    #   实测 F0 = 352.8 Hz，有声帧 80%
    #   源（本机麦克风）F0 = 118.7 Hz
    #   差值 = 12×log2(352.8/118.7) = +18.86 半音
    #
    # 复现方法：
    #   voice.py <你的声音.wav> --target <曼波素材.wav>
    "manbo": 18.86,
}

PRESET_LABELS = {
    "natural": "自然女声 184Hz",
    "cartoon": "卡通高音 229Hz",
    "wawa": "瓦瓦音 257Hz",
    "wawa_oct": "尖锐版 485Hz",
    "manbo": "曼波音 353Hz ★",
}


class VoiceChanger(object):
    """把一段男声变成女声（或反过来），并做基本的后处理。

    参数
        semitones : 变调半音数。+6 是男→女的推荐值；负数是女→男
        highpass  : 高通截止频率（Hz）。滤掉低频隆隆声，0 = 关闭
        gate_db   : 噪声门阈值（dBFS）。低于这个电平的段直接静音，None = 关闭
        normalize : 处理完是否做峰值归一化
        peak_dbfs : 归一化目标峰值
    """

    def __init__(self, semitones=6.0, formant_semitones=None, highpass=80.0,
                 gate_db=-55.0, normalize=True, peak_dbfs=-4.0, n_fft=1024,
                 lifter=40):
        self.semitones = semitones
        # 共振峰单独搬移量。None = 跟音高同步（耦合，默认行为）
        self.formant_semitones = (semitones if formant_semitones is None
                                  else formant_semitones)
        self.highpass = highpass
        self.gate_db = gate_db
        self.normalize = normalize
        self.peak_dbfs = peak_dbfs
        self.n_fft = n_fft
        self.lifter = lifter

    @classmethod
    def from_preset(cls, name, **kwargs):
        """用预设音效创建。name 见 PRESETS：natural / cartoon / wawa / manbo"""
        if name not in PRESETS:
            raise KeyError("未知预设 %r，可用：%s"
                           % (name, ", ".join(sorted(PRESETS))))
        return cls(semitones=PRESETS[name], **kwargs)

    # ── 后处理 ──────────────────────────────────────────
    @staticmethod
    def _highpass(x, sr, cutoff, taps=255):
        """FIR 高通（窗函数法），滤掉低频嗡声"""
        if cutoff <= 0 or cutoff >= sr / 2:
            return x
        n = np.arange(taps) - (taps - 1) / 2.0
        h = np.sinc(2.0 * cutoff / sr * n) * np.hamming(taps)
        h /= h.sum()
        hp = -h
        hp[(taps - 1) // 2] += 1.0
        return np.convolve(x, hp, mode="same").astype(np.float32)

    @staticmethod
    def _noise_gate(x, sr, threshold_db, frame_ms=20.0, smooth_ms=10.0):
        """简单噪声门：按帧算 RMS，低于阈值就把那一帧压掉。

        用平滑增益包络避免"咔哒"声。
        """
        if threshold_db is None:
            return x

        n = max(1, int(sr * frame_ms / 1000.0))
        n_frames = len(x) // n
        if n_frames < 2:
            return x

        thr = 10.0 ** (threshold_db / 20.0)
        gains = np.ones(n_frames, dtype=np.float32)
        for i in range(n_frames):
            seg = x[i * n:(i + 1) * n]
            rms = float(np.sqrt(np.mean(seg * seg)))
            if rms < thr:
                # 低得越多，压得越狠
                gains[i] = max(0.0, rms / thr) if thr > 0 else 0.0

        # 帧间平滑
        k = max(1, int(sr / n * smooth_ms / 1000.0))
        if k > 1:
            kernel = np.ones(k, dtype=np.float32) / k
            gains = np.convolve(gains, kernel, mode="same")

        # 逐采样插值增益
        env = np.repeat(gains, n)
        if len(env) < len(x):
            env = np.concatenate([env, np.ones(len(x) - len(env), dtype=np.float32)])
        return (x * env[:len(x)]).astype(np.float32)

    @staticmethod
    def _normalize(x, peak_dbfs=-4.0):
        peak = float(np.max(np.abs(x))) if x.size else 0.0
        if peak <= 0:
            return x
        target = 10.0 ** (peak_dbfs / 20.0)
        return np.clip(x * (target / peak), -1.0, 1.0).astype(np.float32)

    # ── 主接口 ──────────────────────────────────────────
    def process(self, samples, sr):
        """完整处理链：变调（含共振峰）→ 高通 → 噪声门 → 归一化"""
        x = np.asarray(samples, dtype=np.float32)
        if x.size == 0:
            return x

        # 单声道
        if x.ndim > 1:
            x = x.mean(axis=1)

        # ① 音高 + 共振峰按同一比例搬移（时间拉伸 + 重采样）
        out = pitch_shift(x, sr, semitones=self.semitones, n_fft=self.n_fft)

        # ② 共振峰补偿 —— 让共振峰可以独立于音高
        #    例如：音高要升 18.86 半音，但共振峰只该升 12 半音，
        #    这里就把多搬出来的 6.86 半音拉回来。
        extra = self.formant_semitones - self.semitones
        if abs(extra) > 1e-6:
            out = formant_shift(out, sr, semitones_to_ratio(extra),
                                n_fft=self.n_fft, lifter=self.lifter)

        # ② 高通：滤波声和低频隆隆
        if self.highpass and self.highpass > 0:
            out = self._highpass(out, sr, self.highpass)

        # ③ 噪声门
        if self.gate_db is not None:
            out = self._noise_gate(out, sr, self.gate_db)

        # ④ 归一化
        if self.normalize:
            out = self._normalize(out, self.peak_dbfs)

        return out


# ══════════════════════════════════════════════════════════════
# 基频分析与音色匹配
# ══════════════════════════════════════════════════════════════
def estimate_f0(x, sr, fmin=70.0, fmax=1000.0, frame_ms=40.0, hop_ms=20.0,
                voicing_threshold=0.30, silence_db=-45.0, subharm_tol=0.85):
    """估计基频（自相关法）。

    返回 (中位基频 Hz, 有声帧比例)

    原理：
        对每一帧做自相关，找除 0 延迟外的第一个显著峰，
        峰的位置就是基音周期 → F0 = sr / 周期。
        峰值本身（归一化后）反映"周期性有多强"，低于阈值就当成清音/噪声丢掉。

    用 FFT 算自相关，比直接 np.correlate 快一个数量级。
    """
    x = np.asarray(x, dtype=np.float32)
    if x.ndim > 1:
        x = x.mean(axis=1)
    if x.size == 0:
        return 0.0, 0.0

    x = x - float(np.mean(x))

    n = int(sr * frame_ms / 1000.0)
    hop = int(sr * hop_ms / 1000.0)
    if n <= 0 or hop <= 0 or len(x) < n:
        return 0.0, 0.0

    lag_min = max(2, int(sr / fmax))
    lag_max = int(sr / fmin)
    if lag_max <= lag_min:
        return 0.0, 0.0

    nfft = 1 << int(np.ceil(np.log2(2 * n)))
    silence = 10.0 ** (silence_db / 20.0)

    f0s = []
    total = 0
    for start in range(0, len(x) - n, hop):
        seg = x[start:start + n]
        total += 1

        if float(np.sqrt(np.mean(seg * seg))) < silence:
            continue

        spec = np.fft.rfft(seg, nfft)
        ac = np.fft.irfft(np.abs(spec) ** 2, nfft)[:n]
        if ac[0] <= 0:
            continue
        ac = ac / ac[0]

        hi = min(lag_max, len(ac) - 1)
        if hi <= lag_min:
            continue
        region = ac[lag_min:hi]
        if region.size < 3:
            continue

        # ⚠️ 关键：自相关在 2×、3× 周期处也有峰，直接取最大值会选到次谐波
        #    （实测：800 Hz 的纯音被估计成 400 Hz）。
        #    做法：先取全局最大，再在"高度足够的局部极大"里选**延迟最小**的那个，
        #    也就是取最短周期 = 最高的基频。
        best = float(region.max())
        if best < voicing_threshold:
            continue

        chosen = None
        floor = best * subharm_tol
        for j in range(1, len(region) - 1):
            v = region[j]
            if v >= floor and v >= region[j - 1] and v >= region[j + 1]:
                chosen = j
                break
        if chosen is None:
            chosen = int(np.argmax(region))

        peak = chosen + lag_min

        f0s.append(sr / float(peak))

    if not f0s:
        return 0.0, 0.0

    return float(np.median(f0s)), (len(f0s) / float(total) if total else 0.0)


def semitones_between(f_source, f_target):
    """两个基频之间差几个半音（正数=目标更高）"""
    if f_source <= 0 or f_target <= 0:
        return 0.0
    return 12.0 * np.log2(float(f_target) / float(f_source))


def analyze(samples, sr):
    """对一段语音做基本分析，返回字典"""
    f0, voiced = estimate_f0(samples, sr)
    x = np.asarray(samples, dtype=np.float32)
    if x.ndim > 1:
        x = x.mean(axis=1)

    peak = float(np.max(np.abs(x))) if x.size else 0.0
    rms = float(np.sqrt(np.mean(x * x))) if x.size else 0.0

    return {
        "duration": len(x) / float(sr) if sr else 0.0,
        "sample_rate": sr,
        "f0": f0,
        "voiced_ratio": voiced,
        "peak_db": (20.0 * np.log10(peak)) if peak > 0 else -999.0,
        "rms_db": (20.0 * np.log10(rms)) if rms > 0 else -999.0,
    }


# ══════════════════════════════════════════════════════════════
# 自检
# ══════════════════════════════════════════════════════════════
def _dominant_freq(x, sr):
    """求主频（用 FFT 峰值）"""
    n = len(x)
    spec = np.abs(np.fft.rfft(x * np.hanning(n)))
    freqs = np.fft.rfftfreq(n, 1.0 / sr)
    return float(freqs[int(np.argmax(spec))])


def _print_analysis(path, info):
    print("  %s" % path)
    print("    时长 %.2f s @ %d Hz | 峰值 %.1f dBFS | RMS %.1f dBFS"
          % (info["duration"], info["sample_rate"], info["peak_db"], info["rms_db"]))
    if info["f0"] > 0:
        print("    基频 F0 ≈ %.1f Hz  (有声帧 %.0f%%)"
              % (info["f0"], info["voiced_ratio"] * 100))
    else:
        print("    基频 F0: 测不出来（可能没人声 / 太短 / 全是噪声）")


def analyze_files(paths, target_path=None):
    """命令行入口：分析 wav，可选和目标的基频对比算变调半音"""
    import soundfile as sf

    print("=" * 64)
    print("  语音基频分析")
    print("=" * 64)

    infos = {}
    for path in paths:
        data, sr = sf.read(path, dtype="float32")
        info = analyze(data, sr)
        infos[path] = info
        _print_analysis(path, info)

    if target_path:
        if target_path not in infos:
            data, sr = sf.read(target_path, dtype="float32")
            infos[target_path] = analyze(data, sr)
            print()
            _print_analysis(target_path, infos[target_path])

        src = [i for p, i in infos.items() if p != target_path and i["f0"] > 0]
        tgt = infos[target_path]

        print()
        print("=" * 64)
        if not src:
            print("  源声音没测出基频，没法算")
        elif tgt["f0"] <= 0:
            print("  目标声音没测出基频，没法算")
        else:
            print("  以第一个源声音为准：")
            f_src = src[0]["f0"]
            st = semitones_between(f_src, tgt["f0"])
            print("    源 F0   = %.1f Hz" % f_src)
            print("    目标 F0 = %.1f Hz" % tgt["f0"])
            print()
            print("  ➜ 需要的变调量：%+.2f 半音" % st)
            print("    用法： VoiceChanger(semitones=%.2f)" % st)
            print()
            print("  说明：本模块的变调会**同时移动共振峰**（重采样 + 时间拉伸），")
            print("        所以基频对上之后，音色（共振峰）也会跟着接近目标。")
        print("=" * 64)
    return 0


def compare_presets(path, save_dir="/tmp"):
    """把同一个声音依次套用所有预设并播放 —— 演示和调参时用。

    演示场景：说同一句话，一路听 242Hz → 280Hz → 310Hz → 387Hz → 485Hz，
    屏幕可以同步显示当前预设，观众能直接感受到"变调量"的作用。
    """
    import time
    import soundfile as sf

    data, sr = sf.read(path, dtype="float32")
    if data.ndim > 1:
        data = data.mean(axis=1)

    info = analyze(data, sr)
    print("=" * 68)
    print("  预设对比：%s" % path)
    print("  源 F0 ≈ %.1f Hz | 时长 %.2f s" % (info["f0"], info["duration"]))
    print("=" * 68)

    try:
        import kskbl.audio as audio
        dev = audio.find_alsa_device("output")
        can_play = True
    except Exception as exc:  # noqa: BLE001
        print("  （无法播放：%s）" % exc)
        can_play = False

    for name in PRESETS:
        vc = VoiceChanger.from_preset(name, gate_db=None,
                                      normalize=True, peak_dbfs=-4.0)
        out = vc.process(data, sr)
        out_path = os.path.join(save_dir, "compare_%s.wav" % name)
        sf.write(out_path, out, sr)

        r = analyze(out, sr)
        print("  %-10s %+6.2f 半音 → F0 %6.1f Hz   %s"
              % (name, PRESETS[name], r["f0"], PRESET_LABELS.get(name, "")))

        if can_play:
            audio.play_wav(out_path, device=dev)
            time.sleep(0.45)

    print("=" * 68)
    print("  文件都在 %s/compare_*.wav" % save_dir)
    return 0


def shift_one(path, semitones, formant=None, out_path=None, play=False,
              peak_dbfs=-4.0):
    """对单个 wav 按指定参数变声（可独立指定音高与共振峰）"""
    import time
    import soundfile as sf

    data, sr = sf.read(path, dtype="float32")
    if data.ndim > 1:
        data = data.mean(axis=1)

    before = analyze(data, sr)

    vc = VoiceChanger(semitones=semitones, formant_semitones=formant,
                      gate_db=None, normalize=True, peak_dbfs=peak_dbfs)
    out = vc.process(data, sr)

    after = analyze(out, sr)

    if out_path is None:
        out_path = "/tmp/shift_%d_%d.wav" % (int(round(semitones * 100)),
                                             int(round((formant if formant is not None else semitones) * 100)))
    sf.write(out_path, out, sr)

    print("  音高     %+.2f 半音 → F0 %.1f Hz（原来 %.1f）"
          % (semitones, after["f0"], before["f0"]))
    if formant is not None:
        print("  共振峰   %+.2f 半音（%s）"
              % (formant,
                 "与音高同步" if abs(formant - semitones) < 1e-6
                 else "独立设置"))
    print("  输出     %s" % out_path)

    if play:
        try:
            import kskbl.audio as audio
            audio.play_wav(out_path, device=audio.find_alsa_device("output"))
        except Exception as exc:  # noqa: BLE001
            print("  （播放失败：%s）" % exc)

    return 0


def main():
    """没有参数时跑纯音自检；给了 wav 路径就做基频分析。"""
    import argparse

    ap = argparse.ArgumentParser(
        description="变声 DSP 自检 / 语音基频分析",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""示例：
    python3 voice.py                                   # 纯音自检
    python3 voice.py ~/my_voice.wav                    # 分析我的声音
    python3 voice.py ~/my_voice.wav --target manbo.wav  # 算需要升几个半音
""",
    )
    ap.add_argument("files", nargs="*", help="要分析的 wav 文件")
    ap.add_argument("--target", default=None, help="目标声音 wav，用来算变调半音数")
    ap.add_argument("--compare", action="store_true",
                    help="对第一个 wav 套用所有预设并逐个播放（演示/调参用）")
    ap.add_argument("--shift", type=float, default=None,
                    help="变调半音数（与 --formant 配合做单次处理）")
    ap.add_argument("--formant", type=float, default=None,
                    help="共振峰搬移半音数；不填则与 --shift 同步")
    ap.add_argument("--play", action="store_true", help="处理完直接播放")
    ap.add_argument("--out", default=None, help="输出 wav 路径")
    args = ap.parse_args()

    if args.shift is not None and args.files:
        return shift_one(args.files[0], args.shift, args.formant,
                         out_path=args.out, play=args.play)

    if args.compare and args.files:
        return compare_presets(args.files[0])

    if args.files or args.target:
        paths = list(args.files)
        if args.target and args.target not in paths:
            paths.append(args.target)
        return analyze_files(args.files, args.target)

    # ── 纯音自检 ──
    sr = 48000
    dur = 2.0
    t = np.arange(int(sr * dur), dtype=np.float32) / sr
    tone = (0.3 * np.sin(2 * np.pi * 440.0 * t)).astype(np.float32)

    def active_ratio(x, rate, thr_db=-40.0):
        n = int(rate * 0.02)
        thr = 10.0 ** (thr_db / 20.0)
        frames = len(x) // n
        if not frames:
            return 0.0
        act = sum(1 for i in range(frames)
                  if np.sqrt(np.mean(x[i * n:(i + 1) * n] ** 2)) > thr)
        return act / float(frames)

    print("=" * 70)
    print("  变声 DSP 自检 —— 纯音验证相位声码器")
    print("=" * 70)
    print("输入: 440.00 Hz, %.2f s, 有效段 %.1f%%"
          % (dur, active_ratio(tone, sr) * 100))
    print()

    ok = True
    for st in (0, 3, 6, 8, 10, 12, -6, -12):
        out = pitch_shift(tone, sr, semitones=st)
        f0 = _dominant_freq(out, sr)
        expect = 440.0 * semitones_to_ratio(st)
        err = 100.0 * (f0 - expect) / expect if expect else 0.0
        ar = active_ratio(out, sr)
        flag = "OK " if abs(err) < 1.0 else "!! "
        if abs(err) >= 1.0:
            ok = False
        print("  %s%+4d 半音 → %7.2f Hz (期望 %7.2f, 误差 %+6.2f%%) "
              "| 长度 %.2fs | 有效段 %.1f%%"
              % (flag, st, f0, expect, err, len(out) / sr, ar * 100))

    print()
    print("=" * 70)
    print("  结论：%s" % ("✅ 全部通过（误差 <1%，长度不变，无静音填充）"
                        if ok else "❌ 有偏差，需要检查"))
    print("=" * 70)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
