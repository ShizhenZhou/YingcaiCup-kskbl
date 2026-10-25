#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""主状态机 —— 把屏幕、录音、语音识别、语音合成串成一个完整链路

────────────────────────────────────────────────────────
流程
────────────────────────────────────────────────────────
    待机 IDLE
       ↓ 触发（自动循环 / 3.5mm 插孔 / 回车）
    录音 LISTENING     屏幕显示音量条
       ↓
    识别 THINKING      屏幕显示「识别中…」
       ↓
    展示 RESULT        屏幕显示识别出来的文字
       ↓
    回应 SPEAKING      TTS 合成 + 播放，屏幕显示回复
       ↓
    回到待机

────────────────────────────────────────────────────────
⚠️ 这一步故意**还没做变声**
────────────────────────────────────────────────────────
    先把「说完 → 认出来 → 回一句」这条链路跑通，
    变声 DSP 是下一步。格式上给变声预留了位置（见 process()）。

────────────────────────────────────────────────────────
用法
────────────────────────────────────────────────────────
    # 自动循环（最省事，适合无人值守测试）
    python3 src/kskbl/app.py --trigger auto --cycles 3

    # 插 3.5mm 插头触发（板子自己的 ES8316 孔，现在空着）
    python3 src/kskbl/app.py --trigger jack

    # 听完就退出，不做回应（只看识别效果）
    python3 src/kskbl/app.py --trigger auto --cycles 1 --no-tts

    python3 src/kskbl/app.py --help
"""

import argparse
import os
import re
import subprocess
import sys
import time

# 允许直接用 python3 src/kskbl/app.py 运行（而不只是 python -m kskbl.app）
if __package__ in (None, ""):  # pragma: no cover
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import kskbl.audio as audio                              # noqa: E402
from kskbl.asr import Recognizer                          # noqa: E402
from kskbl.render import screens                          # noqa: E402
from kskbl.st7789 import ST7789                           # noqa: E402
from kskbl.cosy import CosyVoice                        # noqa: E402
from kskbl.tts import Synthesizer                         # noqa: E402
from kskbl.voice import PRESETS, PRESET_LABELS, VoiceChanger  # noqa: E402


# 状态常量
IDLE = "idle"
LISTENING = "listening"
THINKING = "thinking"
RESULT = "result"
SPEAKING = "speaking"
ERROR = "error"


# ── 语音切换音色的指令 ────────────────────────────────────────
# 说「我是曼波」→ 切到曼波音色（CosyVoice 音色克隆）
# 说「我是瓦瓦」→ 切到瓦瓦音色（DSP 波形变声）
#
# ⚠️ ASR 对这两个名字的识别**非常不稳定**，实测收到的写法有：
#      「我是慢播」「我是漫播」「我是漫步」「我是娃娃」「我是哇哇」
#    "曼"同音字有 曼/慢/漫/满/蛮， "波"同音字有 波/播/拨/博/步，
#    穷举是 5×5=25 种组合，而且以后还会遇到新的。
#
#    所以改用**字符类正则**：只要相邻两个字符分别落在同音字集合里就算命中。
#    不要求"我是"前缀（句子里出现这个词就当成指令）。
_VOICE_PATTERNS = [
    # 「我是曼波」的各种同音写法
    ("manbo", re.compile("[曼慢漫满蛮蔓][波播拨博簸步]")),
    ("wawa", re.compile("[瓦娃哇挖袜][瓦娃哇挖袜]")),

    # 「切换到音色1」/「音色一」。
    # ASR 常把"音色"识别成同音的"银色"，数字也可能写成汉字，所以都覆盖。
    ("manbo", re.compile("[音银][色瑟]\\s*[1一壹]")),
    ("wawa", re.compile("[音银][色瑟]\\s*[2二贰]")),
]

# 同义词（整个梗的其他叫法）
VOICE_ALIASES = [
    ("manbo", ("哈基米",)),
]

# ── 关机指令 ──────────────────────────────────────────────
# 「关机」只有两个字，ASR 很容易在别的句子里误命中，
# 一旦误触发板子就断电了。所以采取**整句严格匹配**：
#   去掉标点后，整句必须正好等于"关机"（可以带礼貌前后缀）。
#   像「我要关机充电」「关机键在哪」这种**不会**触发。
SHUTDOWN_WORDS = ("关机", "关闭机器", "关掉机器", "关闭系统")
SHUTDOWN_PREFIXES = ("请", "帮我", "可以", "麻烦", "快", "现在")
SHUTDOWN_SUFFIXES = ("吧", "了", "啦", "谢谢", "一下", "吧谢谢")


def detect_shutdown_command(text):
    """检测是否在要求关机。只有整句表达这个意思才返回 True。

    >>> detect_shutdown_command("关机")        -> True
    >>> detect_shutdown_command("关机吧！")     -> True
    >>> detect_shutdown_command("请关机")      -> True
    >>> detect_shutdown_command("我要关机充电") -> False
    >>> detect_shutdown_command("关机键在哪")   -> False
    """
    if not text:
        return False

    flat = "".join(text.split())
    flat = flat.strip(" .,。，!！?？…、;；:：\"'")

    # 反复剥掉礼貌前后缀，最后必须正好剩下一个关机词
    for _ in range(3):
        changed = False
        for p in SHUTDOWN_PREFIXES:
            if flat.startswith(p) and len(flat) > len(p):
                flat = flat[len(p):]
                changed = True
        for s in SHUTDOWN_SUFFIXES:
            if flat.endswith(s) and len(flat) > len(s):
                flat = flat[:-len(s)]
                changed = True
        if not changed:
            break

    return flat in SHUTDOWN_WORDS


# 这些音色走 CosyVoice 音色克隆（文字→语音），其余走 DSP 波形变声
COSSY_VOICES = ("manbo",)

VOICE_LABELS = {
    "manbo": "曼波音色",
    "wawa": "瓦瓦音色",
    "wawo_oct": "尖锐版",
    "natural": "自然女声",
    "cartoon": "卡通高音",
    "off": "不变声",
}


def detect_voice_command(text):
    """从识别文本里找语音切换指令，返回音色名或 None。

    支持两种说法（每种都容忍同音字）：
        「我是曼波」      == 「切换到音色1」  → manbo
        「我是瓦瓦」      == 「切换到音色2」  → wawa

    >>> detect_voice_command("我是慢播。")          -> "manbo"
    >>> detect_voice_command("切换到音色1")        -> "manbo"
    >>> detect_voice_command("切换到银色一")        -> "manbo"
    >>> detect_voice_command("切换到音色2")        -> "wawa"
    >>> detect_voice_command("今天天气不错")        -> None
    """
    if not text:
        return None
    flat = "".join(text.split())
    for name, pattern in _VOICE_PATTERNS:
        if pattern.search(flat):
            return name
    for name, words in VOICE_ALIASES:
        for word in words:
            if word in flat:
                return name
    return None


class App(object):
    def __init__(self, args):
        self.args = args
        self.state = IDLE

        print("=" * 62)
        print("  会听会变声的智能终端 —— 语音链路状态机")
        print("=" * 62)

        # ── 屏幕 ──
        print("\n[1/4] 初始化屏幕")
        self.lcd = ST7789(speed_hz=args.speed, width=240, height=240)
        self.lcd.init()
        self.screens = screens
        print("  SPI %.1f MHz，尺寸 %dx%d" % (args.speed / 1e6,
                                             self.lcd.width, self.lcd.height))

        # ── 音频设备 ──
        print("\n[2/4] 准备音频设备")
        self.card = audio.find_usb_card_index()
        if self.card is None:
            raise SystemExit("❌ 找不到 USB 声卡")

        audio.setup_mic_gain(self.card, gain=args.mic_gain)
        audio.setup_speaker(self.card, volume=args.speaker_volume)

        self.dev_in = audio.find_sounddevice_index("input")
        if self.dev_in is None:
            raise SystemExit("❌ 找不到 USB 声卡的录音设备")

        self.recorder = audio.Recorder(device=self.dev_in, rate=audio.RECORD_RATE)
        print("  ALSA 卡号 %d | 录音设备索引 %s | %d Hz 单声道"
              % (self.card, self.dev_in, audio.RECORD_RATE))
        print("  麦克风增益 %d/35，扬声器音量 %d/37" % (args.mic_gain, args.speaker_volume))

        # ── AI 模型 ──
        print("\n[3/4] 加载语音识别模型")
        t0 = time.time()
        self.asr = Recognizer(num_threads=args.asr_threads)
        print("  SenseVoice 加载完成，%.2f s" % self.asr.load_elapsed)

        # ── 语音合成（MeloTTS）──
        # 现在不再用 TTS 复述识别结果（改成直接播变声后的声音），
        # 所以默认**不加载** MeloTTS —— 能省 8 秒启动时间。
        # 需要时加 --tts 启用。
        self.tts = None
        if args.tts:
            print("\n[4/4] 加载语音合成模型")
            t0 = time.time()
            self.tts = Synthesizer()
            print("  MeloTTS 加载完成，%.2f s" % (time.time() - t0))
        else:
            print("\n[4/4] 跳过语音合成（默认不加载，需要时加 --tts）")

        # ── 变声器 / 音色克隆引擎 ──
        # 两套引擎，用 switch_voice() 统一切换：
        #   DSP 预设（natural/cartoon/wawa/…） → VoiceChanger 波形变声，保留语调
        #   manbo                              → CosyVoice 音色克隆，像特定人
        self.vc = None
        self.cosy = None
        self.current_voice = args.voice
        self.voice_label = args.voice
        self.switch_voice(args.voice)

    # ── 屏幕 ───────────────────────────────────────────────
    def show(self, buf):
        self.lcd.show(buf)

    def show_error(self, message, detail=""):
        self.state = ERROR
        self.show(self.screens.error(message, detail))
        print("  ⚠️  %s  %s" % (message, detail))

    # ── 触发 ───────────────────────────────────────────────
    def wait_trigger(self):
        """等一次触发。返回 True 表示该开工了。"""
        if self.args.trigger == "auto":
            time.sleep(self.args.gap)
            return True

        if self.args.trigger == "jack":
            return self._wait_jack()

        if self.args.trigger == "enter":
            try:
                input("\n按回车开始一轮（Ctrl+C 退出）… ")
                return True
            except EOFError:
                return False

        raise SystemExit("未知触发方式：%s" % self.args.trigger)

    def _wait_jack(self):
        """等 3.5mm 插孔插入（板子自己的 ES8316，card 3）

        实机验证：/dev/input/event8 上报 SW_HEADPHONE_INSERT
        """
        import select
        import struct

        EVENT_FMT = struct.Struct("llHHi")
        EV_SW = 0x05
        SW_HEADPHONE_INSERT = 0x02

        event_path = self.args.jack_event
        if not os.path.exists(event_path):
            raise SystemExit("❌ 找不到插孔事件设备 %s" % event_path)

        fd = os.open(event_path, os.O_RDONLY)
        print("\n等待 3.5mm 插孔触发（插一下就开始）…")
        self.show(self.screens.idle("插入 3.5mm 插头", footer="等待触发"))

        buf = b""
        try:
            while True:
                ready, _, _ = select.select([fd], [], [], 0.3)
                if not ready:
                    continue
                buf += os.read(fd, EVENT_FMT.size * 32)
                hit = False
                while len(buf) >= EVENT_FMT.size:
                    _, _, etype, code, value = EVENT_FMT.unpack_from(buf, 0)
                    buf = buf[EVENT_FMT.size:]
                    if etype == EV_SW and code == SW_HEADPHONE_INSERT and value == 1:
                        hit = True
                if hit:
                    return True
        finally:
            os.close(fd)

    # ── 一轮认知 ───────────────────────────────────────────
    def listen(self):
        """录音阶段，返回 float32 波形"""
        self.state = LISTENING
        levels = []

        # 有静音自动停时，时长上限只是保险；否则是固定录音时长
        if self.args.silence_stop and self.args.silence_stop > 0:
            hint = "说完停顿 %d 秒自动结束" % int(self.args.silence_stop)
        else:
            hint = "请说话，%d 秒…" % self.args.duration

        # ── 给用户明确的「开始说话」信号 ──
        # 3 声倒数的嘀（800Hz），最后一声长音（1400Hz）就是"开始说"。
        # 没有提示音的话，人根本不知道什么时候该说（实测踩过这个坑：
        # 前几次录音全是环境噪声，ASR 还因此编出一段通顺的假话）。
        out_dev = audio.find_alsa_device("output")
        for i in (3, 2, 1):
            self.show(self.screens.busy("准备录音…", "倒数 %d —— 待会儿听到长音就开始说" % i))
            audio.beep(times=1, freq=800, duration=0.15, volume=0.5, device=out_dev)
            time.sleep(0.85)

        self.show(self.screens.listening(levels, hint=hint))
        audio.beep(times=1, freq=1400, duration=0.35, volume=0.6, device=out_dev)
        if self.args.silence_stop and self.args.silence_stop > 0:
            print("\n🔔 长音已响 —— 现在开始说话（说完停顿 %.1f 秒自动结束，最多 %.1f 秒）！"
                  % (self.args.silence_stop, self.args.duration))
        else:
            print("\n🔔 长音已响 —— 现在开始说话（%.1f 秒）！" % self.args.duration)

        def on_level(ratio, progress):
            levels.append(ratio)
            if len(levels) > 64:
                del levels[:-64]
            self.show(self.screens.listening(levels, hint=hint))

        print("🎤 录音中…")
        t0 = time.time()
        samples = self.recorder.record(
            self.args.duration, level_cb=on_level, tick=0.12,
            silence_stop=self.args.silence_stop or None)
        info = getattr(self.recorder, "last_info", {})

        # 两声低音 = 录音结束
        audio.beep(times=2, freq=520, duration=0.12, volume=0.5, device=out_dev)
        print("🔔 提示音：录音结束")
        raw_len = len(samples) / audio.RECORD_RATE
        samples = audio.trim_silence(samples, audio.RECORD_RATE)
        print("  录到 %.2f 秒（%s），裁掉首尾静音后 %.2f 秒"
              % (raw_len, info.get("reason", "?"),
                 len(samples) / audio.RECORD_RATE))

        # 统计电平
        import math

        peak = float(abs(samples).max()) if len(samples) else 0.0
        rms = float((samples ** 2).mean() ** 0.5) if len(samples) else 0.0
        peak_db = 20 * math.log10(peak) if peak > 0 else -999.0
        rms_db = 20 * math.log10(rms) if rms > 0 else -999.0
        print("  峰值 %.1f dBFS | RMS %.1f dBFS | 峰值-RMS 差 %.1f dB"
              % (peak_db, rms_db, peak_db - rms_db))

        # 存盘便于事后分析
        if self.args.save_wav:
            import soundfile as sf
            sf.write(self.args.save_wav, samples, audio.RECORD_RATE)
            print("  已存到 %s" % self.args.save_wav)

        return samples

    def recognize(self, samples):
        """识别阶段"""
        self.state = THINKING
        self.show(self.screens.busy("识别中…", "SenseVoice"))
        print("🧠 识别中…")
        r = self.asr.recognize(samples, audio.RECORD_RATE)
        print("  耗时 %.3f s | %.1f 倍速 | 语种 %s" % (
            r.elapsed, r.realtime_factor, r.lang))
        print("  识别文字: %s" % (r.text or "(空)"))
        return r

    # ── 音色切换 ───────────────────────────────────────────
    def switch_voice(self, name, quiet=False):
        """切换当前音色。

        name 可以是 DSP 预设名（natural/cartoon/wawa/wawa_oct/off），
        也可以是 COSSY_VOICES 里的名字（manbo）。
        """
        self.current_voice = name
        self.vc = None
        self.cosy = None

        if name == "off":
            self.voice_label = VOICE_LABELS.get("off", "不变声")
            if not quiet:
                print("🎛  变声：关闭")
            return

        if name in COSSY_VOICES:
            self.voice_label = VOICE_LABELS.get(name, name)
            try:
                self.cosy = CosyVoice.from_preset(
                    name, root=self.args.cosy_root, out_dir=self.args.cosy_out)
                missing = self.cosy.check()
                if missing:
                    raise RuntimeError("缺少 %s" % missing[0])
                print("🎙  音色：%s（CosyVoice 音色克隆）" % self.voice_label)
            except Exception as exc:  # noqa: BLE001
                print("⚠️  无法启用「%s」：%s" % (name, exc))
                print("   回退到瓦瓦音色")
                self.current_voice = "wawa"
                self.voice_label = VOICE_LABELS.get("wawa", "wawa")
                self.vc = VoiceChanger.from_preset("wawa")
            return

        self.vc = VoiceChanger.from_preset(name)
        self.voice_label = VOICE_LABELS.get(name,
                                            PRESET_LABELS.get(name, name))
        if not quiet:
            print("🎛  变声：%s（%+.2f 半音）"
                  % (self.voice_label, PRESETS[name]))

    def process(self, samples, result):
        """处理阶段 —— 变声 DSP

        用 --voice 选预设：natural / cartoon / wawa / manbo / off
        """
        if self.vc is None:
            return samples

        # 屏幕保持显示识别到的那句话，不切画面（变声只要 0.5 秒，切了反而闪）
        print("🎛  变声：%s" % self.voice_label)
        t0 = time.time()
        out = self.vc.process(samples, audio.RECORD_RATE)
        print("  变声耗时 %.2f s" % (time.time() - t0))
        return out

    def play_changed(self, samples, result):
        """把变声后的声音放出来 —— 屏幕保持显示识别到的那句话"""
        import soundfile as sf

        out = self.args.voice_out
        sf.write(out, samples, audio.RECORD_RATE)
        print("🔊 播放变声结果…")
        audio.play_wav(out, device=audio.find_alsa_device("output"))

    def play_cosy(self, text, result):
        """用 CosyVoice 音色克隆把这句话念出来（曼波音色走这条路）

        屏幕保持显示识别到的那句话，不做额外装饰。
        """
        self.state = SPEAKING
        print("🎙  音色克隆（%s）：%s" % (self.voice_label, text))

        # 屏幕上：上半屏显示要念的文字，下半屏显示进度条（10~20 秒，别让人干等）
        def on_progress(ratio):
            self.show(self.screens.speech_progress(text, ratio))

        self.show(self.screens.speech_progress(text, 0.0))
        wav, metrics = self.cosy.synthesize(text, on_progress=on_progress)
        print("  合成耗时 %.2f s | 音频 %s s | 持续 RTF %s"
              % (metrics.get("elapsed", 0),
                 metrics.get("audio_seconds", "?"),
                 metrics.get("steady_pcm_rtf", "?")))

        # 合成完成 → 进度条直接跳到 100%，给个"做完了"的明确反馈
        self.show(self.screens.speech_progress(text, 1.0, title="合成完成"))
        time.sleep(0.4)

        audio.play_wav(wav, device=audio.find_alsa_device("output"))

    def reply(self, result):
        """生成回应文本"""
        text = (result.text or "").strip()
        if not text:
            return "我没有听清楚，请再说一遍。"

        if self.args.reply == "fixed":
            return "我听到了。"

        # 默认：回声式，证明识别和合成两端都通
        prefix = "你说的是："
        body = text.rstrip("。.!？?")
        return prefix + body + "。"

    def speak(self, text):
        """合成并播放"""
        if self.tts is None:
            return None

        self.state = SPEAKING
        self.show(self.screens.busy("正在回应…", "合成中"))

        out = self.args.tts_out
        info = self.tts.say(text, path=out, play=False,
                            peak_dbfs=self.args.tts_peak)

        # 播放时把文字显示出来
        self.show(self.screens.text_page(
            "回应",
            text,
            footer="%.1f s 音频  %.1f 倍速"
                   % (info["duration"],
                      (info["duration"] / info["synth_elapsed"])
                      if info["synth_elapsed"] else 0),
            title_color=(120, 230, 160),
        ))
        audio.play_wav(out, device=audio.find_alsa_device("output"))

        print("🔊 播放回应：%s" % text)
        return info

    def shutdown(self):
        """执行关机。

        ⚠️ 这是破坏性操作：选检测函数时已经做了整句严格匹配，
           这里再给屏幕和两声低音做最后确认，然后调用系统关机。
        """
        print("\n🛑 收到关机指令")
        self.show(self.screens.plain("关机中…"))

        dev = audio.find_alsa_device("output")
        audio.beep(times=1, freq=400, duration=0.25, volume=0.6, device=dev)
        time.sleep(0.35)
        audio.beep(times=1, freq=300, duration=0.45, volume=0.6, device=dev)

        ok = False
        for cmd in (["sudo", "-n", "poweroff"],
                    ["sudo", "-n", "systemctl", "poweroff"]):
            try:
                r = subprocess.run(cmd, capture_output=True, text=True,
                                   timeout=20)
                if r.returncode == 0:
                    print("  已发出关机命令：%s" % " ".join(cmd))
                    ok = True
                    break
                print("  %s 失败（exit %d）：%s"
                      % (" ".join(cmd), r.returncode,
                         (r.stderr or r.stdout or "").strip()[:200]))
            except Exception as exc:  # noqa: BLE001
                print("  %s 异常：%s" % (" ".join(cmd), exc))

        if not ok:
            print("  ⚠️ 关机失败。请确认 sudoers 已配置：")
            print("     sudo bash deploy/install_service.sh install")
            self.show(self.screens.plain("关机失败"))
            time.sleep(2.0)
            return

        # 关机过程中不要再去录下一轮；系统几秒后会断电
        time.sleep(30)

    # ── 主循环 ─────────────────────────────────────────────
    def run(self):
        self.show(self.screens.idle("待机中", footer="触发方式：%s" % self.args.trigger))
        print("\n" + "=" * 62)
        print("  ✅ 就绪，屏幕已显示待机画面")
        print("=" * 62)

        cycle = 0
        try:
            while True:
                if self.args.cycles and cycle >= self.args.cycles:
                    print("\n已跑满 %d 轮，结束" % self.args.cycles)
                    break

                if not self.wait_trigger():
                    break
                cycle += 1
                print("\n" + "─" * 62)
                print("第 %d 轮" % cycle)
                print("─" * 62)

                try:
                    samples = self.listen()
                    result = self.recognize(samples)

                    text = (result.text or "").strip()
                    # 去掉标点后为空 = 根本没识别到语音
                    # （ASR 在低信噪比下经常只回一个「.」，此时不能拿去做 TTS，
                    #   否则会合成出「你说的是：。」这种没有内容的句子）
                    if len(text.strip(" .。,，!！?？…、;；:：")) == 0:
                        print("  （识别结果只有标点，按「无语音」处理）")
                        self.show_error("没有识别到语音",
                                        "听到长音提示后立刻说话，声音大一点")
                        time.sleep(2.0)
                        last = getattr(self, "last_text", "")
                        self.show(self.screens.idle(
                            "待机中",
                            footer=("上次：" + last[:26]) if last
                            else "AI 语音链路就绪"))
                        continue

                    # ── 关机指令 ──
                    # 必须整句表达关机才触发（「我要关机充电」不会命中），
                    # 见 detect_shutdown_command 的说明。
                    if detect_shutdown_command(text):
                        self.shutdown()
                        return

                    # ── 语音切换音色的指令 ──
                    # 只有**确实要换**音色时才走切换流程（这句话本身不变声）。
                    # 如果当前已经是这个音色，说明用户在说一句普通的话，
                    # 那就照常把整句念出来 —— 不切换也不吞掉。
                    cmd = detect_voice_command(text)
                    if cmd is not None and cmd != self.current_voice:
                        print("\n🎚 检测到音色切换指令 → %s" % cmd)
                        # 切换时说了一句什么不重要，屏幕上直接显示「切换中…」
                        self.show(self.screens.plain("切换中…"))
                        self.switch_voice(cmd)
                        self.last_text = text
                        audio.beep(times=2, freq=900, duration=0.10, gap=0.08,
                                   volume=0.5,
                                   device=audio.find_alsa_device("output"))
                        time.sleep(1.2)
                        continue

                    if cmd is not None:
                        # 已经是这个音色 —— 当普通句子照常处理
                        print("\n🎚 提到「%s」，但当前已是该音色，按普通句子处理" % cmd)

                    # 屏幕上只显示识别到的那句话，不加标题、页脚、装饰
                    self.state = RESULT
                    self.last_text = text
                    self.show(self.screens.plain(text))
                    print("  📺 显示：%s" % text)
                    time.sleep(0.3)

                    # ── 按当前音色选择引擎 ──
                    if self.current_voice in COSSY_VOICES and self.cosy is not None:
                        # 曼波音色：把你说的这句话用曼波音色念出来
                        self.play_cosy(text, result)
                    else:
                        # DSP 波形变声（保留语调）
                        processed = self.process(samples, result)
                        if self.vc is not None:
                            self.play_changed(processed, result)

                except KeyboardInterrupt:
                    raise
                except Exception as exc:  # noqa: BLE001
                    import traceback
                    traceback.print_exc()
                    self.show_error("出错了", "%s: %s" % (type(exc).__name__, exc))
                    time.sleep(2.0)

                # 回到待机，把上次识别的文字留在屏幕底部
                last = getattr(self, "last_text", "")
                self.show(self.screens.idle(
                    "待机中",
                    footer=("上次：" + last[:26]) if last else "AI 语音链路就绪"))

        except KeyboardInterrupt:
            print("\n\n收到 Ctrl+C，退出中…")
        finally:
            self.close()

    def close(self):
        print("清理资源…")
        try:
            self.lcd.close()
        except Exception:
            pass
        for name in ("asr", "tts"):
            obj = getattr(self, name, None)
            if obj is not None:
                try:
                    obj.close()
                except Exception:
                    pass
        print("已退出")


def main():
    ap = argparse.ArgumentParser(
        description="语音链路状态机：待机 → 录音 → 识别 → 回应",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--trigger", default="auto",
                    choices=["auto", "jack", "enter"],
                    help="触发方式（默认 auto 自动循环）")
    ap.add_argument("--gap", type=float, default=1.0, help="auto 模式下每轮间隔秒数")
    ap.add_argument("--cycles", type=int, default=0, help="跑几轮后结束（0=一直跑）")
    ap.add_argument("--duration", type=float, default=15.0,
                    help="最长录音时长（秒）。有 --silence-stop 时是上限")
    ap.add_argument("--silence-stop", type=float, default=3.0,
                    help="说完后停顿多少秒自动结束录音（默认 3；0 = 关闭，用固定时长）")
    ap.add_argument("--result-hold", type=float, default=1.5,
                    help="识别结果显示停留时间（秒）")
    ap.add_argument("--reply", default="echo", choices=["echo", "fixed"],
                    help="回应方式：echo=复述识别文本，fixed=固定一句话")
    ap.add_argument("--tts", action="store_true",
                    help="加载 MeloTTS 做语音合成（默认不加载，省 8 秒启动）")
    ap.add_argument("--no-tts", action="store_true",
                    help="兼容旧参数，现在默认就是不加载")
    ap.add_argument("--voice", default="wawa",
                    choices=sorted(set(list(PRESETS.keys())
                                       + list(COSSY_VOICES) + ["off"])),
                    help="当前音色（默认 wawa）。"
                         "natural/cartoon/wawa/wawa_oct = DSP 波形变声（保留语调）；"
                         "manbo = CosyVoice 音色克隆（像曼波，但丢语调）；off = 不变声")
    ap.add_argument("--cosy-root", default=None,
                    help="CosyVoice 项目根目录（默认 ~/proj/cosyvoice2_rknn）")
    ap.add_argument("--cosy-out", default="/tmp/cosy_app",
                    help="CosyVoice 合成输出目录")
    ap.add_argument("--voice-out", default="/tmp/app_voice.wav",
                    help="变声结果的输出 wav")
    ap.add_argument("--tts-out", default="/tmp/app_tts.wav", help="TTS 输出文件")
    ap.add_argument("--tts-peak", type=float, default=-4.0,
                    help="TTS 归一化目标峰值 dBFS（默认 -4，数值越小越轻）")
    ap.add_argument("--save-wav", default=None,
                    help="把每次录音存到这个 wav（调试用）")
    ap.add_argument("--mic-gain", type=int, default=25, help="麦克风增益 0~35（默认 25）")
    ap.add_argument("--speaker-volume", type=int, default=37, help="扬声器音量 0~37")
    ap.add_argument("--speed", type=int, default=30_000_000, help="SPI 时钟 Hz")
    ap.add_argument("--asr-threads", type=int, default=4, help="ASR 线程数")
    ap.add_argument("--jack-event", default="/dev/input/event8",
                    help="jack 模式下的插孔事件设备")
    args = ap.parse_args()

    App(args).run()


if __name__ == "__main__":
    main()
