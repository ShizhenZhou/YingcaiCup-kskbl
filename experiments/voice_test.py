#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
变声算法测试脚本
用法:
    python voice_test.py                  # 用合成的"啊"元音测试
    python voice_test.py 你的录音.wav      # 用真实录音测试（单声道/双声道 WAV 都行）

运行前先激活虚拟环境:
    source ~/proj/venv/bin/activate
"""
import sys
import os
import wave

import numpy as np
import sounddevice as sd

SR = 48000          # 目标采样率
OUTDIR = os.path.expanduser("~/proj/audio")


# ============================================================
#  工具函数
# ============================================================
def find_output_device():
    """按名字找 ES8316（板载 3.5mm 耳机口）。找不到就返回 None 用系统默认。"""
    try:
        for i, d in enumerate(sd.query_devices()):
            name = d["name"].lower()
            if "es8316" in name and d["max_output_channels"] > 0:
                return i
    except Exception as e:
        print("  查询音频设备失败:", e)
    return None


def load_wav(path):
    """读 WAV，转成 float32 单声道，重采样到 SR"""
    with wave.open(path, "rb") as w:
        nch = w.getnchannels()
        sw = w.getsampwidth()
        sr = w.getframerate()
        n = w.getnframes()
        raw = w.readframes(n)

    if sw == 2:
        x = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    elif sw == 1:
        x = (np.frombuffer(raw, dtype=np.uint8).astype(np.float32) - 128) / 128.0
    elif sw == 4:
        x = np.frombuffer(raw, dtype="<i4").astype(np.float32) / 2147483648.0
    else:
        raise ValueError("不支持的位深: %d 字节" % sw)

    if nch > 1:                                   # 多声道 -> 取平均变单声道
        x = x.reshape(-1, nch).mean(axis=1)

    if sr != SR:                                  # 简单线性重采样
        n_out = int(len(x) * SR / sr)
        x = np.interp(np.arange(n_out) * sr / SR, np.arange(len(x)), x).astype(np.float32)
    return x


def save_wav(path, x, sr=SR):
    """写 16bit 单声道 WAV"""
    y = np.clip(x, -1.0, 1.0)
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes((y * 32767).astype("<i2").tobytes())


def play(x, device=None, label=""):
    print("  ▶ 播放 %s (%.1f 秒)" % (label, len(x) / SR))
    sd.play(np.ascontiguousarray(x, dtype=np.float32), SR, device=device)
    sd.wait()


# ============================================================
#  测试信号：合成一个"啊"的元音
# ============================================================
def synth_vowel(f0=120.0, dur=2.5, sr=SR):
    """
    源-滤波器模型：
      声源 = 脉冲串(声带振动) + 少量噪声
      声道 = 三个共振峰(模拟"啊"的频谱包络)
    """
    n = int(sr * dur)
    src = np.zeros(n)
    src[::int(sr / f0)] = 1.0                     # 基频脉冲串
    src += 0.01 * np.random.randn(n)              # 一点气声

    out = np.zeros(n)
    f = np.fft.rfftfreq(n, 1.0 / sr)
    X = np.fft.rfft(src)
    # "啊" 的三个共振峰 (中心频率Hz, 带宽Hz, 增益)
    for fc, bw, g in ((700, 90, 1.0), (1200, 110, 0.5), (2600, 160, 0.25)):
        H = g / (1.0 + 1j * (f - fc) / bw)         # 单极点谐振器
        out += np.fft.irfft(X * H, n=n)

    out /= (np.max(np.abs(out)) + 1e-9)
    return (out * 0.6).astype(np.float32)


# ============================================================
#  变声效果
# ============================================================
def fx_robot(x, fc=50.0, depth=1.0):
    """机器人音：环形调制（把信号乘以低频正弦）"""
    t = np.arange(len(x)) / SR
    return (x * (1 - depth) + x * depth * np.cos(2 * np.pi * fc * t)).astype(np.float32)


def fx_pitch(x, factor):
    """
    升/降调：改变重采样率。
    factor > 1 -> 升调（变短，像萝莉/花栗鼠）
    factor < 1 -> 降调（变长，像大叔/怪物）
    注意：这个方法会同时改变时长，属于"能用但不完美"的简易实现。
    """
    n_out = int(len(x) / factor)
    idx = np.arange(n_out) * factor
    return np.interp(idx, np.arange(len(x)), x).astype(np.float32)


# ============================================================
#  主流程
# ============================================================
def main():
    os.makedirs(OUTDIR, exist_ok=True)

    # --- 1. 拿到输入信号 ---
    if len(sys.argv) > 1:
        src_path = sys.argv[1]
        print("读入录音:", src_path)
        x = load_wav(src_path)
        tag = "录音"
    else:
        print("未指定文件，使用合成的'啊'元音测试信号")
        x = synth_vowel()
        tag = "合成元音"

    print("  时长 %.2f 秒 / 采样率 %d Hz / 峰值 %.3f" %
          (len(x) / SR, SR, float(np.max(np.abs(x)))))

    # --- 2. 生成各个版本 ---
    variants = [
        ("原始", x),
        ("机器人音", fx_robot(x, fc=50.0)),
        ("升调 x1.40 (萝莉)", fx_pitch(x, 1.40)),
        ("降调 x0.70 (大叔)", fx_pitch(x, 0.70)),
    ]

    for name, y in variants:
        p = os.path.join(OUTDIR, "test_%s.wav" % name.replace(" ", "_").replace("/", ""))
        save_wav(p, y)
        print("  💾 %s  ->  %s  (%.1f 秒)" % (name, p, len(y) / SR))

    # --- 3. 播放 ---
    dev = find_output_device()
    print("\n输出设备:", dev if dev is not None else "系统默认")
    print("戴上耳机，注意听差异。Ctrl+C 可中断。\n")

    for name, y in variants:
        play(y, device=dev, label=name)

    print("\n完成。生成的 WAV 在 %s" % OUTDIR)


if __name__ == "__main__":
    main()
