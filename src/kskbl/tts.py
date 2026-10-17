# -*- coding: utf-8 -*-
"""语音合成（TTS）—— MeloTTS-RKNN2（RKNN NPU）+ 响度自动归一化

把文字变成语音。中文为主，中英混排也能读。

────────────────────────────────────────────────────────
性能实测（ROCK 5B+）
────────────────────────────────────────────────────────
    encoder（ONNX CPU）   ~80 ms     ← 作者说编码器没上 NPU
    decoder（RKNN NPU）   ~200 ms/切片
    合计约 4.5~4.7 倍速（7.82 s 音频 → 1.68 s 推理）

    一次性开销：模型加载 2.5 s + RKNN 初始化 ~3 s
    → **所以必须做成常驻对象，反复调用 synthesize()，
       千万不要每次重新构造 Synthesizer**

────────────────────────────────────────────────────────
⚠️ 响度问题（必须处理）
────────────────────────────────────────────────────────
    模型原始输出峰值只有 -18 ~ -20 dBFS，听起来明显偏小。
    本模块的 synthesize() 之后请调用 normalize()，
    或者直接用 say()（内部已经归一化到 -1 dBFS）。

────────────────────────────────────────────────────────
⚠️ 许可证
────────────────────────────────────────────────────────
    MeloTTS-RKNN2 是 **AGPL-3.0**。
    本模块只以「把模型目录加进 sys.path 然后 import」的方式调用它，
    **没有把它的源码复制进本仓库** —— 这样许可证边界最清楚。
    如果以后要把它打包进发布物，需要单独评估 AGPL 义务。

────────────────────────────────────────────────────────
用法
────────────────────────────────────────────────────────
    from kskbl.tts import Synthesizer

    tts = Synthesizer()              # 加载一次（约 6 秒）
    audio = tts.synthesize("你好，世界")
    audio = tts.normalize(audio)     # 响度归一到 -1 dBFS
    tts.save("/tmp/out.wav", audio)

    # 一步到位（合成 + 归一化 + 存盘，可选播放）
    tts.say("你好，世界", path="/tmp/out.wav", play=True)

    tts.close()

模型目录结构（见 README）：
    /home/radxa/proj/MeloTTS-RKNN2/
        encoder.onnx  decoder.rknn  g.bin
        lexicon.txt   tokens.txt
        melotts_rknn.py  text/  english_utils/  utils.py
"""

import contextlib
import io
import os
import re
import subprocess
import sys
import time

import numpy as np
import soundfile as sf


# 默认模型位置
DEFAULT_TTS_DIR = "/home/radxa/proj/MeloTTS-RKNN2"
DEFAULT_SPEED = 0.8          # 语速；>1 更快，<1 更慢（作者默认 0.8）
DEFAULT_SAMPLE_RATE = 44100

# 解码器每次最长处理 65536 帧，每帧 512 采样
DEC_LEN = 65536 // 512       # = 128


def find_usb_sound_card():
    """在 /proc/asound/cards 里找 USB 声卡，返回 ALSA 卡 ID。

    ⚠️ 声卡号会漂移（见过 4 → 5 → 3），所以不能用编号，要用卡 ID。
    """
    try:
        with open("/proc/asound/cards") as fh:
            for line in fh:
                if "USB-Audio" not in line:
                    continue
                match = re.match(r"\s*\d+\s+\[([^\]]*)\]", line)
                if match:
                    return match.group(1).strip()
    except OSError:
        pass
    return None


class Synthesizer(object):
    """MeloTTS-RKNN2 合成器。

    构造一次，之后反复调 synthesize()。**不要反复构造**。
    """

    def __init__(self, model_dir=DEFAULT_TTS_DIR, speed=DEFAULT_SPEED,
                 sample_rate=DEFAULT_SAMPLE_RATE, verbose=False):
        self.model_dir = os.path.abspath(model_dir)
        self.speed = speed
        self.sample_rate = sample_rate
        self.verbose = verbose

        if not os.path.isdir(self.model_dir):
            raise FileNotFoundError("找不到 MeloTTS 目录：%s" % self.model_dir)

        # 让 melotts_rknn / utils / text 能被 import
        if self.model_dir not in sys.path:
            sys.path.insert(0, self.model_dir)

        import onnxruntime as ort
        from rknnlite.api import RKNNLite

        for name in ("encoder.onnx", "decoder.rknn", "g.bin",
                     "lexicon.txt", "tokens.txt"):
            path = os.path.join(self.model_dir, name)
            if not os.path.exists(path):
                raise FileNotFoundError("缺少模型文件：%s" % path)

        t0 = time.time()

        with self._quiet():
            import melotts_rknn as mel
            self._mel = mel

            # 编码器：ONNX Runtime，跑 CPU
            self.sess_enc = ort.InferenceSession(
                os.path.join(self.model_dir, "encoder.onnx"),
                providers=["CPUExecutionProvider"],
                sess_options=ort.SessionOptions(),
            )

            # 解码器：RKNN，跑 NPU
            self.decoder = RKNNLite()
            ret = self.decoder.load_rknn(os.path.join(self.model_dir, "decoder.rknn"))
            if ret != 0:
                raise RuntimeError("加载 decoder.rknn 失败（ret=%d）" % ret)
            ret = self.decoder.init_runtime()
            if ret != 0:
                raise RuntimeError("RKNN init_runtime 失败（ret=%d）" % ret)

            # 静态输入 g
            self.g = np.fromfile(os.path.join(self.model_dir, "g.bin"),
                                 dtype=np.float32).reshape(1, 256, 1)

            # 词典
            self.lexicon = mel.Lexicon(
                os.path.join(self.model_dir, "lexicon.txt"),
                os.path.join(self.model_dir, "tokens.txt"),
            )

        self.load_elapsed = time.time() - t0

    # ── 内部 ────────────────────────────────────────────────
    @contextlib.contextmanager
    def _quiet(self):
        """把模型里那些 print 收起来（verbose=False 时）"""
        if self.verbose:
            yield
            return
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            yield

    def _synthesize_sentence(self, se, speed):
        """合成一句话，返回 float32 波形"""
        mel = self._mel
        dec_len = DEC_LEN

        # 文字 → 音素 + 声调
        phone_str, yinjie_num, phones, tones = self.lexicon.convert(se)

        # 词间插空格
        phone_str = mel.intersperse(phone_str, 0)
        phones = np.array(mel.intersperse(phones, 0), dtype=np.int32)
        tones = np.array(mel.intersperse(tones, 0), dtype=np.int32)
        yinjie_num = np.array(yinjie_num, dtype=np.int32) * 2
        yinjie_num[0] += 1
        pron_slices = mel.generate_pronounce_slice(yinjie_num)

        phone_len = phones.shape[-1]
        # language=3 是中文（MeloTTS 内部约定）
        language = np.array([3] * phone_len, dtype=np.int32)

        # 编码器
        z_p, pronoun_lens, audio_len = self.sess_enc.run(None, input_feed={
            "phone": phones,
            "g": self.g,
            "tone": tones,
            "language": language,
            "noise_scale": np.array([0], dtype=np.float32),
            "length_scale": np.array([1.0 / speed], dtype=np.float32),
            "noise_scale_w": np.array([0], dtype=np.float32),
            "sdp_ratio": np.array([0], dtype=np.float32),
        })
        audio_len = int(audio_len[0])

        # z_p 补到 dec_len 的整数倍
        actual_size = z_p.shape[-1]
        dec_slice_num = int(np.ceil(actual_size / dec_len))
        z_p = np.pad(z_p,
                     pad_width=((0, 0), (0, 0),
                                (0, dec_slice_num * dec_len - actual_size)),
                     mode="constant", constant_values=0)

        pron_num = mel.generate_word_pron_num(pronoun_lens, pron_slices)
        pron_num_slices, zp_slices, strip_flags, _pron_lens, is_long = \
            mel.generate_decode_slices(pron_num, dec_len)

        # 解码器（NPU）逐片合成
        sub_audio_list = []
        for i in range(len(pron_num_slices)):
            pron_start, pron_end = pron_num_slices[i]
            zp_start, zp_end = zp_slices[i]

            if is_long[i]:
                sub_audio_list.extend(
                    mel.decode_long_word(self.decoder,
                                         z_p[..., zp_start:zp_end],
                                         self.g, dec_len))
                continue

            sub_dec_len = zp_end - zp_start
            sub_audio_len = 512 * sub_dec_len

            zp_slice = z_p[..., zp_start:zp_end]
            if zp_slice.shape[-1] < dec_len:
                zp_slice = np.concatenate(
                    (zp_slice,
                     np.zeros((*zp_slice.shape[:-1],
                               dec_len - zp_slice.shape[-1]), dtype=np.float32)),
                    axis=-1)

            outputs = self.decoder.inference(inputs=[zp_slice, self.g])
            audio = outputs[0].flatten()[:sub_audio_len]

            # 去掉切片重叠的部分
            if strip_flags[i][0]:
                audio = audio[512 * pron_num[pron_start]:]
            if strip_flags[i][1]:
                audio = audio[:-512 * pron_num[pron_end - 1]]

            sub_audio_list.append(audio)

        return mel.merge_sub_audio(sub_audio_list, 0, audio_len)

    # ── 对外接口 ────────────────────────────────────────────
    def synthesize(self, text, speed=None):
        """文字 → float32 波形（还没归一化）"""
        if not text or not text.strip():
            return np.zeros(0, dtype=np.float32)

        mel = self._mel
        speed = self.speed if speed is None else speed

        sentences = mel.split_sentences_zh(text)
        pieces = []
        with self._quiet():
            for se in sentences:
                if not se.strip():
                    continue
                pieces.append(self._synthesize_sentence(se, speed))
            if not pieces:
                return np.zeros(0, dtype=np.float32)
            audio = mel.audio_numpy_concat(pieces, sr=self.sample_rate, speed=speed)

        return np.asarray(audio, dtype=np.float32)

    @staticmethod
    def normalize(audio, peak_dbfs=-1.0):
        """峰值归一化到指定 dBFS。

        模型原始输出峰值约 -18 dBFS，听起来偏小；
        归一化到 -1 dBFS 大约加 17 dB。
        """
        a = np.asarray(audio, dtype=np.float32)
        if a.size == 0:
            return a

        peak = float(np.max(np.abs(a)))
        if peak <= 0.0:
            return a

        target = 10.0 ** (peak_dbfs / 20.0)
        gain = target / peak
        return np.clip(a * gain, -1.0, 1.0)

    def save(self, path, audio):
        """保存为 WAV"""
        sf.write(path, np.asarray(audio, dtype=np.float32), self.sample_rate)
        return path

    def play(self, path):
        """用 aplay 播到 USB 声卡"""
        card = find_usb_sound_card()
        device = ("plughw:%s,0" % card) if card else "default"
        subprocess.run(["aplay", "-q", "-D", device, path], check=False)
        return device

    def say(self, text, path=None, play=False, normalize=True,
            peak_dbfs=-1.0, speed=None):
        """一步到位：合成 →（归一化）→（存盘）→（播放）

        返回 dict：audio / path / synth_elapsed / duration
        """
        t0 = time.time()
        audio = self.synthesize(text, speed=speed)
        synth_elapsed = time.time() - t0

        if normalize and audio.size:
            audio = self.normalize(audio, peak_dbfs)

        if path:
            self.save(path, audio)

        device = None
        if play:
            if not path:
                raise ValueError("play=True 时必须给 path（要先存成 wav 才能播）")
            device = self.play(path)

        return {
            "audio": audio,
            "path": path,
            "device": device,
            "synth_elapsed": synth_elapsed,
            "duration": len(audio) / float(self.sample_rate) if self.sample_rate else 0.0,
        }

    def close(self):
        try:
            if getattr(self, "decoder", None) is not None:
                self.decoder.release()
        except Exception:
            pass
        self.decoder = None

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        self.close()


def main():
    """命令行自检：合成一句固定的话并播放"""
    import argparse

    ap = argparse.ArgumentParser(description="MeloTTS 语音合成自检")
    ap.add_argument("text", nargs="?", default="你好，这是一次语音合成的自检。")
    ap.add_argument("--model-dir", default=DEFAULT_TTS_DIR)
    ap.add_argument("--speed", type=float, default=DEFAULT_SPEED)
    ap.add_argument("--out", default="/tmp/tts_selftest.wav")
    ap.add_argument("--play", action="store_true", help="合成后播放")
    ap.add_argument("--verbose", action="store_true", help="显示模型内部日志")
    args = ap.parse_args()

    tts = Synthesizer(model_dir=args.model_dir, speed=args.speed,
                      verbose=args.verbose)
    print("模型加载完成，耗时 %.2f s" % tts.load_elapsed)

    info = tts.say(args.text, path=args.out, play=args.play)
    print("\n文本  : %s" % args.text)
    print("合成  : 耗时 %.2f s，生成 %.2f s 音频，%.1f 倍速"
          % (info["synth_elapsed"], info["duration"],
             (info["duration"] / info["synth_elapsed"]) if info["synth_elapsed"] else 0))
    print("输出  : %s" % args.out)
    if info["device"]:
        print("播放  : %s" % info["device"])

    tts.close()


if __name__ == "__main__":
    main()
