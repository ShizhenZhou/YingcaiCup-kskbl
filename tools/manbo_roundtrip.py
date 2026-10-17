#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""往返测试：我说话 → SenseVoice 识别 → CosyVoice 用曼波音色念出来 → 播放

这是**音色迁移**，不是波形变声：
    音色来自曼波参考音频，但语调和节奏会丢失（中间过了一道文字）。

真正的波形变声在 src/kskbl/app.py 的 --voice 预设里（瓦瓦音等）。

用法：
    ~/proj/venv/bin/python tools/manbo_roundtrip.py
    ~/proj/venv/bin/python tools/manbo_roundtrip.py --duration 8 --mic-gain 28
"""
import argparse
import os
import subprocess
import sys
import time

sys.path.insert(0, "/home/radxa/proj/kskbl/src")

import numpy as np            # noqa: E402
import kskbl.audio as audio   # noqa: E402
from kskbl.asr import Recognizer  # noqa: E402

COSY_ROOT = "/home/radxa/proj/cosyvoice2_rknn"
RKNN = os.path.join(COSY_ROOT, "rknn")


def run_cosyvoice(text, profile, runtime, fixture, out_dir):
    """调 C++ 流水线合成，返回 wav 路径（失败返回 None）"""
    os.makedirs(out_dir, exist_ok=True)
    text_path = os.path.join(out_dir, "target.txt")
    with open(text_path, "w", encoding="utf-8") as f:
        f.write(text)

    env = dict(os.environ)
    env.update({
        "NEXTTTS_ROOT": RKNN,
        "PIPELINE_BIN": os.path.join(RKNN, "cpp", "build",
                                     "cosyvoice2_streaming_pipeline"),
        "VOICE_PROFILE_ROOT": profile,
        "RUNTIME_ROOT": runtime,
        "FLOW_FIXTURE": fixture,
        "TEXT_FILE": text_path,
        "OUTPUT_ROOT": out_dir,
        "PCM_STREAM": os.path.join(out_dir, "audio.s16le"),
    })

    script = os.path.join(RKNN, "scripts", "run_cosyvoice2_streaming_rk3588.sh")
    t0 = time.time()
    proc = subprocess.run(["bash", script, "sample"], env=env,
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                          text=True, timeout=900)
    elapsed = time.time() - t0

    wav = os.path.join(out_dir, "streaming_segment00.wav")
    ok = proc.returncode == 0 and os.path.exists(wav)
    metrics = {}
    for line in (proc.stdout or "").splitlines():
        if "=" in line and line.split("=")[0].strip().isidentifier():
            k, _, v = line.partition("=")
            metrics[k.strip()] = v.strip()

    print("     合成耗时 %.2f s | audio_seconds=%s | steady_rtf=%s"
          % (elapsed, metrics.get("audio_seconds", "?"),
             metrics.get("steady_pcm_rtf", "?")))
    if not ok:
        print("     ❌ 合成失败，最后的输出：")
        for line in (proc.stdout or "").splitlines()[-12:]:
            print("        " + line)
        return None
    return wav


def main():
    ap = argparse.ArgumentParser(description="说话 → ASR → 曼波音色念出来")
    ap.add_argument("--duration", type=float, default=6.0, help="录音秒数")
    ap.add_argument("--mic-gain", type=int, default=28, help="麦克风增益")
    ap.add_argument("--profile", default="/tmp/reg_manbo/voice_profile")
    ap.add_argument("--runtime", default="/tmp/reg_manbo/runtime_input")
    ap.add_argument("--fixture", default="/tmp/reg_manbo/flow_fixture")
    ap.add_argument("--out", default="/tmp/cosy_live")
    ap.add_argument("--models", default="/home/radxa/proj/models/sense_voice")
    args = ap.parse_args()

    for d in (args.profile, args.runtime, args.fixture):
        if not os.path.isdir(d):
            print("❌ 缺少音色资产：%s" % d)
            print("   先跑 tools/cosy_register.py 注册音色")
            return 2

    card = audio.find_usb_card_index()
    out_dev = audio.find_alsa_device("output")
    in_dev = audio.find_sounddevice_index("input")
    audio.setup_mic_gain(card, gain=args.mic_gain)
    print("声卡 %s | 录音设备 %s | 播放设备 %s" % (card, in_dev, out_dev))

    print("\n加载识别模型…")
    t0 = time.time()
    asr = Recognizer(model_dir=args.models, num_threads=4)
    print("  就绪（%.2f s）" % (time.time() - t0))

    try:
        # ── 倒数提示音 ──
        for i in (3, 2, 1):
            print("  倒数 %d…" % i)
            audio.beep(times=1, freq=800, duration=0.15, volume=0.5, device=out_dev)
            time.sleep(0.85)
        audio.beep(times=1, freq=1400, duration=0.35, volume=0.6, device=out_dev)
        print("\n🔔 长音已响 —— 现在开始说话（%.0f 秒）！" % args.duration)

        # ── 录音 ──
        rec = audio.Recorder(device=in_dev)
        samples = rec.record(args.duration)
        audio.beep(times=2, freq=520, duration=0.12, volume=0.5, device=out_dev)
        print("🔔 录音结束")

        peak = float(np.max(np.abs(samples))) if len(samples) else 0.0
        rms = float(np.sqrt(np.mean(samples ** 2))) if len(samples) else 0.0
        db = lambda v: (20 * np.log10(v)) if v > 0 else -999.0
        print("  峰值 %.1f dBFS | RMS %.1f dBFS" % (db(peak), db(rms)))

        # ── 识别 ──
        print("\n🧠 识别中…")
        r = asr.recognize(samples, audio.RECORD_RATE)
        text = (r.text or "").strip()
        print("  耗时 %.3f s | 语种 %s" % (r.elapsed, r.lang))
        print("  识别文字: %r" % text)

        if len(text.strip(" .。,，!！?？…、;；:：")) == 0:
            print("\n❌ 没有识别到语音，请听到长音后立刻说话")
            return 1

        # ── 合成 ──
        print("\n🎙 用曼波音色合成…")
        wav = run_cosyvoice(text, args.profile, args.runtime, args.fixture, args.out)
        if wav is None:
            return 1

        # ── 播放 ──
        print("\n🔊 播放：")
        audio.play_wav(wav, device=out_dev)
        print("\n✅ 完成")
        return 0
    finally:
        asr.close()


if __name__ == "__main__":
    raise SystemExit(main())
