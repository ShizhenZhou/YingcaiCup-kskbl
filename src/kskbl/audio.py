# -*- coding: utf-8 -*-
"""音频输入输出 —— USB 声卡（C-Media 0d8c:0014）

────────────────────────────────────────────────────────
⚠️ 一次性必须做的事：设置麦克风录音增益
────────────────────────────────────────────────────────
    这颗芯片的「Mic Capture Volume」**出厂是 0（−12 dB）**，
    不设置的话录出来是**纯静音**。而且这个设置重启就丢。

    模块里的 setup_mic_gain() 会做这件事，App 启动时必须调一次。

────────────────────────────────────────────────────────
⚠️ 声卡号会漂移
────────────────────────────────────────────────────────
    实测同一块板子重启几次，USB 声卡的编号在 3/4/5 之间变过。
    所以**所有地方都按名字查找**，不用写死编号。

    /proc/asound/cards  →  给 amixer/aplay 用的**卡号**
    sounddevice 的设备列表  →  给 Python 录音用的**设备索引**
    这两个编号是各自独立的一套，要分别查。

────────────────────────────────────────────────────────
用法
────────────────────────────────────────────────────────
    import kskbl.audio as audio

    card = audio.find_usb_card_index()
    audio.setup_mic_gain(card, gain=25)

    dev = audio.find_sounddevice_index("input")
    rec = audio.Recorder(device=dev)
    samples = rec.record(4.0, level_cb=lambda lvl, prog: ...)

    audio.play_wav("/tmp/a.wav")
"""

import os
import re
import subprocess
import time

import numpy as np

try:
    import sounddevice as sd
except ImportError as exc:  # pragma: no cover
    raise ImportError(
        "缺少 sounddevice，请执行：\n"
        "    ~/proj/venv/bin/pip install sounddevice"
    ) from exc


# 录音参数
RECORD_RATE = 48000
RECORD_CHANNELS = 1

# 这颗 C-Media 芯片的混音器控制编号（实机确认，同一颗芯片固定）
NUMID = {
    "speaker_switch": 5,      # BOOLEAN, 扬声器开关
    "speaker_volume": 6,      # 0..37
    "mic_capture_switch": 7,  # BOOLEAN, 麦克风录音开关
    "mic_capture_volume": 8,  # 0..35  (−12 dB .. +23 dB)  ← **出厂是 0！**
    "agc": 9,                 # BOOLEAN, 自动增益
    "mic_playback_switch": 3, # BOOLEAN, 麦克风监听（会啸叫，一般关）
    "mic_playback_volume": 4, # 0..31
}

# 音频电平参考（实测）
#   安静时底噪 RMS ≈ -78 dBFS
#   正常说话 RMS ≈ -51 dBFS（增益 15 时）
LEVEL_FLOOR_DB = -70.0
LEVEL_CEIL_DB = -20.0


# ══════════════════════════════════════════════════════════════
# 设备查找
# ══════════════════════════════════════════════════════════════
def _read_asound_cards():
    """解析 /proc/asound/cards，返回 [(index, card_id, 完整行), ...]"""
    result = []
    try:
        with open("/proc/asound/cards") as fh:
            for line in fh:
                m = re.match(r"\s*(\d+)\s+\[([^\]]*)\]", line)
                if m:
                    result.append((int(m.group(1)), m.group(2).strip(), line.strip()))
    except OSError:
        pass
    return result


def find_usb_card_index():
    """USB 声卡的 ALSA 卡号（给 amixer / aplay 用）"""
    for index, _card_id, line in _read_asound_cards():
        if "USB-Audio" in line:
            return index
    return None


def find_usb_card_id():
    """USB 声卡的 ALSA 卡 ID（例如 'Device'，可以用于 plughw:<id>,0）"""
    for _index, card_id, line in _read_asound_cards():
        if "USB-Audio" in line:
            return card_id
    return None


def find_sounddevice_index(direction="input"):
    """USB 声卡在 sounddevice 里的设备索引

    direction: "input" / "output"
    """
    try:
        devices = sd.query_devices()
    except Exception:
        return None

    for i, d in enumerate(devices):
        if "USB Audio" not in d["name"]:
            continue
        if direction == "input" and d["max_input_channels"] > 0:
            return i
        if direction == "output" and d["max_output_channels"] > 0:
            return i
    return None


def find_alsa_device(direction="output"):
    """给 amixer / aplay / arecord 用的 ALSA 设备串（plughw:XXX,0）"""
    card_id = find_usb_card_id()
    if card_id:
        return "plughw:%s,0" % card_id
    card = find_usb_card_index()
    if card is not None:
        return "plughw:%d,0" % card
    return "default"


# ══════════════════════════════════════════════════════════════
# 混音器设置
# ══════════════════════════════════════════════════════════════
def _amixer(card, *args):
    cmd = ["amixer", "-c", str(card)] + list(args)
    try:
        return subprocess.run(cmd, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, timeout=5.0)
    except Exception:
        return None


def setup_mic_gain(card=None, gain=25, disable_agc=True, disable_monitor=True):
    """设置麦克风录音增益。

    ⚠️ **这一步不做，录音永远是静音。**

    gain: 0..35（−12 dB .. +23 dB）。推荐 20~25：
          太小会淹没在底噪里，太大会削顶（敲咪头就爆音）。
    """
    if card is None:
        card = find_usb_card_index()
    if card is None:
        return False

    _amixer(card, "cset", "numid=%d" % NUMID["mic_capture_volume"], str(gain))
    _amixer(card, "cset", "numid=%d" % NUMID["mic_capture_switch"], "on")
    if disable_agc:
        _amixer(card, "cset", "numid=%d" % NUMID["agc"], "off")
    if disable_monitor:
        _amixer(card, "cset", "numid=%d" % NUMID["mic_playback_switch"], "off")
    return True


def setup_speaker(card=None, volume=37):
    """把扬声器音量开到最大（顺带确保开关打开）"""
    if card is None:
        card = find_usb_card_index()
    if card is None:
        return False
    _amixer(card, "cset", "numid=%d" % NUMID["speaker_switch"], "on")
    _amixer(card, "cset", "numid=%d" % NUMID["speaker_volume"], str(volume))
    return True


# ══════════════════════════════════════════════════════════════
# 电平换算（给音量条用）
# ══════════════════════════════════════════════════════════════
def level_to_ratio(rms):
    """RMS 线性值 → 0~1 的比例（用于画音量条）"""
    if rms <= 0:
        return 0.0
    db = 20.0 * np.log10(rms)
    ratio = (db - LEVEL_FLOOR_DB) / (LEVEL_CEIL_DB - LEVEL_FLOOR_DB)
    return float(max(0.0, min(1.0, ratio)))


# ══════════════════════════════════════════════════════════════
# 录音
# ══════════════════════════════════════════════════════════════
class Recorder(object):
    """固定时长的阻塞录音，期间可以通过回调拿到实时电平。"""

    def __init__(self, device=None, rate=RECORD_RATE, channels=RECORD_CHANNELS,
                 block=1024):
        self.device = device
        self.rate = rate
        self.channels = channels
        self.block = block

    def record(self, seconds, level_cb=None, tick=0.08):
        """录 seconds 秒，返回 float32 单声道数组。

        level_cb(level_ratio, progress)  每 tick 秒被调用一次（**在主线程里**），
        level_ratio 已经是 0~1，progress 是 0~1 的进度。
        """
        frames = int(seconds * self.rate)
        if frames <= 0:
            return np.zeros(0, dtype=np.float32)

        buf = np.zeros(frames, dtype=np.float32)
        state = {"pos": 0, "level": 0.0}

        def callback(indata, nframes, time_info, status):
            n = min(nframes, frames - state["pos"])
            if n <= 0:
                return
            chunk = indata[:n, 0].copy()
            buf[state["pos"]:state["pos"] + n] = chunk
            state["pos"] += n
            state["level"] = float(np.sqrt(np.mean(chunk * chunk)))

        stream = sd.InputStream(
            samplerate=self.rate,
            channels=self.channels,
            dtype="float32",
            device=self.device,
            blocksize=self.block,
            callback=callback,
        )

        t_end = time.monotonic() + seconds
        with stream:
            while True:
                now = time.monotonic()
                if now >= t_end:
                    break
                if level_cb is not None:
                    progress = 1.0 - max(0.0, (t_end - now) / seconds)
                    level_cb(level_to_ratio(state["level"]), progress)
                time.sleep(tick)

        return buf[:state["pos"]]


# ══════════════════════════════════════════════════════════════
# 播放
# ══════════════════════════════════════════════════════════════
def play_wav(path, device=None):
    """用 aplay 播一个 wav（阻塞）"""
    if device is None:
        device = find_alsa_device("output")
    proc = subprocess.run(
        ["aplay", "-q", "-D", device, path],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    return proc.returncode == 0


def beep(times=1, freq=1000.0, duration=0.12, gap=0.09, volume=0.30,
         device=None, sample_rate=48000):
    """响一声（或连响几遍）—— 用来给用户「开始说话」的提示。

    一声高音 = 开始，两声低音 = 结束。

    用纯正弦 + 淡入淡出，不会有爆音。
    """
    import soundfile as sf
    import tempfile

    sr = sample_rate
    parts = []
    for i in range(max(1, times)):
        if i:
            parts.append(np.zeros(int(sr * gap), dtype=np.float32))
        n = int(sr * duration)
        t = np.arange(n, dtype=np.float32) / sr
        tone = (volume * np.sin(2.0 * np.pi * freq * t)).astype(np.float32)
        fade = max(1, int(sr * 0.01))
        if fade * 2 < n:
            tone[:fade] *= np.linspace(0.0, 1.0, fade, dtype=np.float32)
            tone[-fade:] *= np.linspace(1.0, 0.0, fade, dtype=np.float32)
        parts.append(tone)

    if not parts:
        return False
    wave = np.concatenate(parts)

    tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    tmp.close()
    try:
        sf.write(tmp.name, wave, sr)
        return play_wav(tmp.name, device=device)
    finally:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass


def play_samples(samples, sample_rate=RECORD_RATE, device=None):
    """播一段 numpy 波形（内部先写成临时 wav）"""
    import soundfile as sf
    import tempfile

    tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    tmp.close()
    try:
        sf.write(tmp.name, np.asarray(samples, dtype=np.float32), sample_rate)
        return play_wav(tmp.name, device=device)
    finally:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass
