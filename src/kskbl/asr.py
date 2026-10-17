# -*- coding: utf-8 -*-
"""语音识别（ASR）—— SenseVoice / sherpa-onnx

把「说的一段话」转成文字。中英日韩粤自动判别语种，中文英文都准。

────────────────────────────────────────────────────────
为什么用 SenseVoice
────────────────────────────────────────────────────────
    * 非流式（说完再出结果），准确率比流式模型高
    * **自动识别语种**，不用手动切换中英
    * int8 量化版只有 228 MB，加载 2.8 秒
    * 实测速度：20~24 倍速（比实时快一个数量级）

────────────────────────────────────────────────────────
⚠️ 关键：采样率不用自己处理
────────────────────────────────────────────────────────
    SenseVoice 要 16 kHz，但我们录音是 48 kHz。
    sherpa-onnx 会**在内部自动重采样**，日志里能看到：

        offline-stream.cc: AcceptWaveformImpl
          Creating a resampler: 48000 → 16000

    所以直接把 48 kHz 的原始数据丢进来就行。

────────────────────────────────────────────────────────
用法
────────────────────────────────────────────────────────
    from kskbl.asr import Recognizer

    rec = Recognizer()                    # 加载一次（约 3 秒）
    r = rec.recognize_file("/path/a.wav")
    print(r.text, r.lang)

    # 或者直接喂 numpy 数组（麦克风回调里拿到的就是这种）
    r = rec.recognize(samples, 48000)

    rec.close()

模型目录结构：
    /home/radxa/proj/models/sense_voice/
        model.int8.onnx
        tokens.txt
"""

import os
import time

import numpy as np

try:
    import sherpa_onnx
except ImportError as exc:  # pragma: no cover
    raise ImportError(
        "缺少 sherpa-onnx，请执行：\n"
        "    ~/proj/venv/bin/pip install sherpa-onnx"
    ) from exc


# 默认模型位置
DEFAULT_MODEL_DIR = "/home/radxa/proj/models/sense_voice"
DEFAULT_MODEL = "model.int8.onnx"
DEFAULT_TOKENS = "tokens.txt"

# 这些音频模型内部是 16 kHz
MODEL_SAMPLE_RATE = 16000


class AsrResult(object):
    """一次识别的结果。"""

    __slots__ = ("text", "lang", "emotion", "event",
                 "duration", "elapsed", "sample_rate")

    def __init__(self, text, lang="", emotion="", event="",
                 duration=0.0, elapsed=0.0, sample_rate=0):
        self.text = text
        self.lang = lang
        self.emotion = emotion
        self.event = event
        self.duration = duration          # 音频时长（秒）
        self.elapsed = elapsed            # 识别耗时（秒）
        self.sample_rate = sample_rate

    @property
    def realtime_factor(self):
        """实时倍率：音频时长 / 识别耗时"""
        return (self.duration / self.elapsed) if self.elapsed > 0 else 0.0

    def __repr__(self):
        return ("AsrResult(text=%r, lang=%r, duration=%.2fs, elapsed=%.3fs, %.1fx)"
                % (self.text, self.lang, self.duration, self.elapsed,
                   self.realtime_factor))


class Recognizer(object):
    """SenseVoice 离线识别器。

    加载一次模型，之后可以反复调用 recognize()。
    """

    def __init__(self, model_dir=DEFAULT_MODEL_DIR, model=DEFAULT_MODEL,
                 tokens=DEFAULT_TOKENS, use_itn=True, num_threads=4,
                 debug=False):
        self.model_dir = model_dir
        model_path = os.path.join(model_dir, model)
        tokens_path = os.path.join(model_dir, tokens)

        for path, what in ((model_path, "模型"), (tokens_path, "tokens")):
            if not os.path.exists(path):
                raise FileNotFoundError("找不到%s文件：%s" % (what, path))

        self.model_path = model_path
        self.use_itn = use_itn
        self.num_threads = num_threads

        t0 = time.time()
        self._rec = sherpa_onnx.OfflineRecognizer.from_sense_voice(
            model=model_path,
            tokens=tokens_path,
            use_itn=use_itn,
            num_threads=num_threads,
            debug=debug,
        )
        self.load_elapsed = time.time() - t0

    # ── 音频准备 ────────────────────────────────────────────
    @staticmethod
    def _prepare(samples, sample_rate=None):
        """把各种输入统一成 (float32 单声道数组, 采样率)"""
        arr = np.asarray(samples)

        # 整数 PCM → 浮点
        if arr.dtype == np.int16:
            arr = arr.astype(np.float32) / 32768.0
        elif arr.dtype == np.int32:
            arr = arr.astype(np.float32) / 2147483648.0
        elif arr.dtype != np.float32:
            arr = arr.astype(np.float32)

        # 多声道 → 单声道
        if arr.ndim > 1:
            arr = arr.mean(axis=1)

        if sample_rate is None:
            raise ValueError("必须提供 sample_rate（例如 48000）")

        return arr, int(sample_rate)

    # ── 识别 ────────────────────────────────────────────────
    def recognize(self, samples, sample_rate=None):
        """识别一段音频。

        参数
            samples      : numpy 数组（float32 / int16 都行），
                           或任何能被 np.asarray 转成数组的东西
            sample_rate  : 采样率。**不用自己重采样**，内部会自动转到 16 kHz

        返回
            AsrResult
        """
        arr, sr = self._prepare(samples, sample_rate)
        duration = len(arr) / float(sr) if sr else 0.0

        stream = self._rec.create_stream()
        stream.accept_waveform(sr, arr)

        t0 = time.time()
        self._rec.decode_stream(stream)
        elapsed = time.time() - t0

        r = stream.result
        return AsrResult(
            text=getattr(r, "text", ""),
            lang=getattr(r, "lang", ""),
            emotion=getattr(r, "emotion", ""),
            event=getattr(r, "event", ""),
            duration=duration,
            elapsed=elapsed,
            sample_rate=sr,
        )

    def recognize_file(self, path):
        """识别一个 WAV 文件"""
        import soundfile as sf

        data, sr = sf.read(path, dtype="float32")
        return self.recognize(data, sr)

    # ── 资源 ────────────────────────────────────────────────
    def close(self):
        """释放模型（sherpa-onnx 的 Python 对象靠 GC，这里只是给个明确入口）"""
        self._rec = None

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        self.close()


def main():
    """命令行自检：认一下模型自带的测试音频"""
    import argparse

    ap = argparse.ArgumentParser(description="SenseVoice 语音识别自检")
    ap.add_argument("--model-dir", default=DEFAULT_MODEL_DIR)
    ap.add_argument("wav", nargs="*", help="要识别的 wav；不填则用模型自带的测试音频")
    args = ap.parse_args()

    rec = Recognizer(model_dir=args.model_dir)
    print("模型加载完成，耗时 %.2f s" % rec.load_elapsed)

    targets = args.wav or [
        os.path.join(args.model_dir, "test_wavs", "zh.wav"),
        os.path.join(args.model_dir, "test_wavs", "en.wav"),
    ]

    for path in targets:
        if not os.path.exists(path):
            print("\n跳过（不存在）: %s" % path)
            continue
        r = rec.recognize_file(path)
        print("\n=== %s ===" % os.path.basename(path))
        print("  音频 %.2f s @ %d Hz | 识别 %.3f s | %.1f 倍速"
              % (r.duration, r.sample_rate, r.elapsed, r.realtime_factor))
        print("  语言: %s" % r.lang)
        print("  结果: %s" % r.text)

    rec.close()


if __name__ == "__main__":
    main()
