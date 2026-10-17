#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CosyVoice2 RKNN —— 音色注册工具（纯 numpy + onnxruntime，不需要 torch）

用一段参考音频 + 它的转写文字，生成 NPU 流水线需要的全部 voice 资产：

    <profile>/prompt_text.txt                 参考音频的转写文字
    <profile>/prompt_text_token.i32.bin       转写文字的 BPE token
    <profile>/llm_prompt_speech_token.i32.bin 前 1 秒的语音 token（25 个）
    <runtime>/flow_prompt_token_50.i32.bin    末 2 秒的语音 token（50 个）
    <runtime>/prompt_feat_100.f32.bin         末 2 秒的 24k mel（100 帧）
    <fixture>/spks.f32.bin                    说话人向量（80 维）

────────────────────────────────────────────────────────
每一步都对照仓库自带的 ONNX fixture 做过校验（onnx/fixtures/prompt）：

    ① 重采样 44.1k → 16k/24k          corr 0.9999
    ② Kaldi fbank → CampPlus          embedding cos 0.9465
    ③ Whisper log-mel (128)           corr 0.9998
    ④ Speech tokenizer                bit-exact
    ⑤ 24k mel (fmax=8000)             corr 0.9956
    ⑥ BPE 文本 tokenizer              bit-exact

用法：
    python3 cosy_register.py --help
"""

import argparse
import json
import os
import struct
import sys
from fractions import Fraction

import numpy as np
import soundfile as sf
from scipy import signal


# ══════════════════════════════════════════════════════════════
# 文本前端（GPT-2 BPE，与 C++ cosyvoice2_text_frontend 一致）
# ══════════════════════════════════════════════════════════════
_PRETOK = (r"(?i:'s|'t|'re|'ve|'m|'ll|'d)"
           r"|[^\r\n\p{L}\p{N}]?\p{L}+"
           r"|\p{N}"
           r"| ?[^\s\p{L}\p{N}]+[\r\n]*"
           r"|\s*[\r\n]+"
           r"|\s+(?!\S)"
           r"|\s+")


class BpeTokenizer(object):
    """解析 tokenizer.bin 并实现 Qwen2 的 BPE 分词。"""

    def __init__(self, path):
        import regex as re  # noqa: F401  （只需要 unicode 属性支持）

        self._re = re
        data = open(path, "rb").read()
        if data[:8] != b"CV2BPE1\x00":
            raise ValueError("tokenizer magic 不匹配: %r" % data[:8])

        off = [8]

        def u32():
            v = struct.unpack_from("<I", data, off[0])[0]
            off[0] += 4
            return v

        def s():
            n = u32()
            v = data[off[0]:off[0] + n]
            off[0] += n
            return v.decode("utf-8", errors="surrogateescape")

        self.vocab = {}
        for _ in range(u32()):
            tid = u32()
            self.vocab[s()] = tid

        self.ranks = {}
        for r in range(u32()):
            left, right = s(), s()
            self.ranks[(left, right)] = r

        self.specials = []
        for _ in range(u32()):
            tid = u32()
            self.specials.append((tid, s()))

        if off[0] != len(data):
            raise ValueError("tokenizer.bin 有尾部多余数据")

        # GPT-2 byte encoder
        direct = set(range(33, 127)) | set(range(161, 173)) | set(range(174, 256))
        self.byte_enc = [None] * 256
        extra = 0
        for v in range(256):
            if v in direct:
                self.byte_enc[v] = chr(v)
            else:
                self.byte_enc[v] = chr(256 + extra)
                extra += 1

        self._pattern = re.compile(_PRETOK)

    def _bpe(self, piece):
        encoded = "".join(self.byte_enc[b] for b in piece.encode("utf-8"))
        syms = list(encoded)
        while len(syms) > 1:
            best_rank, best = None, None
            for i in range(len(syms) - 1):
                rk = self.ranks.get((syms[i], syms[i + 1]))
                if rk is not None and (best_rank is None or rk < best_rank):
                    best_rank, best = rk, (syms[i], syms[i + 1])
            if best is None:
                break
            merged, i = [], 0
            while i < len(syms):
                if i + 1 < len(syms) and (syms[i], syms[i + 1]) == best:
                    merged.append(syms[i] + syms[i + 1])
                    i += 2
                else:
                    merged.append(syms[i])
                    i += 1
            syms = merged
        return [self.vocab[s_] for s_ in syms]

    def tokenize(self, text):
        out = []
        for m in self._pattern.finditer(text):
            out.extend(self._bpe(m.group(0)))
        return out


# ══════════════════════════════════════════════════════════════
# 音频基础
# ══════════════════════════════════════════════════════════════
def load_mono(path, target_sr):
    """读成单声道并重采样到 target_sr。"""
    data, sr = sf.read(path, dtype="float32", always_2d=True)
    mono = data.mean(axis=1).astype(np.float32)
    if sr != target_sr:
        fr = Fraction(target_sr, sr).limit_denominator(2000)
        mono = signal.resample_poly(mono, fr.numerator, fr.denominator)
        mono = np.ascontiguousarray(mono, dtype=np.float32)
    return mono


# ══════════════════════════════════════════════════════════════
# ① Kaldi fbank（CampPlus 输入）
# ══════════════════════════════════════════════════════════════
def _mel_scale(f):
    return 1127.0 * np.log(1.0 + f / 700.0)


def _inv_mel(m):
    return 700.0 * (np.exp(m / 1127.0) - 1.0)


def _kaldi_banks(num_bins, sr, fft_size, low=20.0, high=None):
    if not high or high <= 0:
        high = 0.5 * sr
    ml, mh = _mel_scale(low), _mel_scale(high)
    delta = (mh - ml) / (num_bins + 1)
    nbins = fft_size // 2 + 1

    def bin_of(f):
        v = f * (fft_size // 2) / (0.5 * sr)
        return int(min(nbins - 1, max(0, int(np.floor(v + 0.5)))))

    banks = np.zeros((num_bins, nbins), dtype=np.float32)
    for i in range(num_bins):
        fl = _inv_mel(ml + i * delta)
        fc = _inv_mel(ml + (i + 1) * delta)
        fr = _inv_mel(ml + (i + 2) * delta)
        a, c, b = bin_of(fl), bin_of(fc), bin_of(fr)
        if c > a:
            banks[i, a:c + 1] = (np.arange(a, c + 1) - a) / (c - a)
        if b > c:
            banks[i, c:b + 1] = (b - np.arange(c, b + 1)) / (b - c)
    return banks


_KALDI_WIN = np.power(0.5 - 0.5 * np.cos(2 * np.pi * np.arange(400) / 399),
                      0.85).astype(np.float32)
_KALDI_BANK = _kaldi_banks(80, 16000, 512)


def kaldi_fbank(x, sr=16000):
    """Kaldi 风格 fbank：25ms 帧长 / 10ms 帧移 / Povey 窗 / 预加重 0.97。"""
    nf = 1 + (len(x) - 400) // 160
    idx = np.arange(400)[None, :] + 160 * np.arange(nf)[:, None]
    fr = x[idx].astype(np.float32)
    fr -= fr.mean(axis=1, keepdims=True)
    fr[:, 1:] -= 0.97 * fr[:, :-1].copy()
    fr *= _KALDI_WIN
    sp = np.fft.rfft(fr, n=512)
    mel = (sp.real ** 2 + sp.imag ** 2) @ _KALDI_BANK.T
    return np.log(np.maximum(mel, 1.1920929e-7)).astype(np.float32)


# ══════════════════════════════════════════════════════════════
# ③ Whisper log-mel（128 维，speech tokenizer 输入）
# ══════════════════════════════════════════════════════════════
def _hz_to_mel(f):
    f = np.asarray(f, dtype=np.float64)
    f_sp = 200.0 / 3.0
    min_log_hz, logstep = 1000.0, np.log(6.4) / 27.0
    return np.where(f >= min_log_hz,
                    min_log_hz / f_sp + np.log(np.maximum(f, 1e-10) / min_log_hz) / logstep,
                    f / f_sp)


def _mel_to_hz(m):
    m = np.asarray(m, dtype=np.float64)
    f_sp = 200.0 / 3.0
    min_log_hz, logstep = 1000.0, np.log(6.4) / 27.0
    min_log_mel = min_log_hz / f_sp
    return np.where(m >= min_log_mel,
                    min_log_hz * np.exp(logstep * (m - min_log_mel)),
                    f_sp * m)


def _slaney_fb(n_mels, sr, n_fft, fmin=0.0, fmax=None):
    if fmax is None:
        fmax = sr / 2.0
    mel_f = _mel_to_hz(np.linspace(_hz_to_mel(fmin), _hz_to_mel(fmax), n_mels + 2))
    fft_f = np.fft.rfftfreq(n_fft, 1.0 / sr)
    fd = np.diff(mel_f)
    ramps = mel_f[:, None] - fft_f[None, :]
    w = np.zeros((n_mels, len(fft_f)))
    for i in range(n_mels):
        w[i] = np.maximum(0.0, np.minimum(-ramps[i] / fd[i], ramps[i + 2] / fd[i + 1]))
    w *= (2.0 / (mel_f[2:n_mels + 2] - mel_f[:n_mels]))[:, None]
    return w.astype(np.float32)


_WHISPER_WIN = signal.get_window("hann", 400).astype(np.float32)
_WHISPER_FB = _slaney_fb(128, 16000, 400)


def whisper_log_mel(x, sr=16000):
    """whisper 的 128 维 log-mel：(1, 128, T)"""
    x = np.pad(x.astype(np.float32), (200, 200), mode="reflect")
    nf = 1 + (len(x) - 400) // 160
    idx = np.arange(400)[None, :] + 160 * np.arange(nf)[:, None]
    sp = np.fft.rfft(x[idx] * _WHISPER_WIN, n=400)
    mag = (sp.real ** 2 + sp.imag ** 2).astype(np.float32).T[:, :-1]
    log = np.log10(np.maximum(_WHISPER_FB @ mag, 1e-10))
    log = np.maximum(log, log.max() - 8.0)
    return ((log + 4.0) / 4.0).astype(np.float32)[None]


# ══════════════════════════════════════════════════════════════
# ⑤ 24k mel（flow prompt 输入）
# ══════════════════════════════════════════════════════════════
_MEL24_NFFT, _MEL24_HOP = 1920, 480
_MEL24_WIN = (0.5 - 0.5 * np.cos(2 * np.pi * np.arange(_MEL24_NFFT) / _MEL24_NFFT)).astype(np.float32)
_MEL24_FB = _slaney_fb(80, 24000, _MEL24_NFFT, 0.0, 8000.0)   # ⚠️ fmax 必须是 8000


def mel24(x):
    """Matcha/CosyVoice 的 80 维 24k mel。返回 (T, 80)。"""
    x = np.clip(np.asarray(x, dtype=np.float32), -1.0, 1.0)
    xp = np.pad(x, (_MEL24_NFFT // 2, _MEL24_NFFT // 2), mode="reflect")
    nf = 1 + (len(xp) - _MEL24_NFFT) // _MEL24_HOP
    idx = np.arange(_MEL24_NFFT)[None, :] + _MEL24_HOP * np.arange(nf)[:, None]
    sp = np.fft.rfft(xp[idx] * _MEL24_WIN, n=_MEL24_NFFT)
    mag = np.abs(sp).astype(np.float32).T
    return np.log(np.maximum(_MEL24_FB @ mag, 1e-5)).T.astype(np.float32)


# ══════════════════════════════════════════════════════════════
# 模型封装
# ══════════════════════════════════════════════════════════════
class FeatureExtractor(object):
    """用 ONNX 模型算 CampPlus 说话人向量和语音 token。"""

    def __init__(self, campplus_path, tokenizer_path):
        import onnxruntime as ort

        opts = ort.SessionOptions()
        opts.intra_op_num_threads = 4
        self.cam = ort.InferenceSession(campplus_path, sess_options=opts,
                                        providers=["CPUExecutionProvider"])
        self.cam_in = self.cam.get_inputs()[0].name
        self.tok = ort.InferenceSession(tokenizer_path, sess_options=opts,
                                        providers=["CPUExecutionProvider"])
        self.tok_inputs = self.tok.get_inputs()

    def speaker_embedding(self, wav16k):
        """返回 192 维说话人向量"""
        f = kaldi_fbank(wav16k)
        f = f - f.mean(axis=0, keepdims=True)
        return self.cam.run(None, {self.cam_in: f[None].astype(np.float32)})[0][0]

    def speech_tokens(self, wav16k):
        """返回语音 token 序列（约 25 个/秒）。

        ⚠️ 这个 ONNX 是 **固定 1753 帧**（17.53 秒）的静态模型，
        所以调用前必须把音频裁/补到 TOKENIZER_SAMPLES_16K 个采样。
        补零会污染 transformer 的注意力（实测只能对上 37/75），
        所以**一定要用足够长的音频**，而不是补零。
        """
        feats = whisper_log_mel(wav16k)
        if feats.shape[2] != TOKENIZER_FRAMES:
            raise ValueError(
                "speech tokenizer 需要恰好 %d 个 whisper 帧（%.2f 秒），"
                "实际 %d 帧（%.2f 秒）。请把音频裁到该长度。"
                % (TOKENIZER_FRAMES, TOKENIZER_FRAMES * 0.01,
                   feats.shape[2], feats.shape[2] * 0.01))
        length = np.array([feats.shape[2]], dtype=np.int32)
        feed = {self.tok_inputs[0].name: feats,
                self.tok_inputs[1].name: length}
        return self.tok.run(None, feed)[0].reshape(-1).astype(np.int32)


# speech tokenizer 的固定输入长度
TOKENIZER_FRAMES = 1753            # whisper 帧数（10 ms/帧）
TOKENIZER_SAMPLES_16K = TOKENIZER_FRAMES * 160     # 280480 采样 = 17.53 s
TOKENIZER_SAMPLES_24K = TOKENIZER_FRAMES * 240     # 420720 采样


def fit_length(x, n):
    """把音频裁/补到恰好 n 个采样（补的是静音）。"""
    if len(x) > n:
        return x[:n]
    if len(x) < n:
        return np.concatenate([x, np.zeros(n - len(x), dtype=np.float32)])
    return x


def count_tokens_for(duration):
    """CosyVoice2 每秒 25 个语音 token（50ms 一个）"""
    return int(round(duration * 25.0))


# ══════════════════════════════════════════════════════════════
# 主流程
# ══════════════════════════════════════════════════════════════
def main():
    ap = argparse.ArgumentParser(
        description="CosyVoice2 RKNN 音色注册（参考音频 + 转写 → NPU 资产）",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--wav", required=True, help="参考音频（整段，用于说话人向量）")
    ap.add_argument("--prompt-wav", default=None,
                    help="prompt 片段（会被裁到 17.53 秒 = tokenizer 固定长度）；"
                         "不填则用 --wav")
    ap.add_argument("--text", default=None, help="prompt 片段的转写文字")
    ap.add_argument("--text-file", default=None, help="从文件读转写文字")
    ap.add_argument("--model-root", required=True,
                    help="models/cosyvoice2 目录")
    ap.add_argument("--onnx-root", required=True,
                    help="onnx 目录（含 models/prompt/*.onnx）")
    ap.add_argument("--out", required=True, help="输出根目录")
    args = ap.parse_args()

    text = args.text
    if text is None and args.text_file:
        text = open(args.text_file, encoding="utf-8").read().strip()
    if not text:
        print("错误：需要 --text 或 --text-file", file=sys.stderr)
        return 2

    if args.prompt_wav is None:
        args.prompt_wav = args.wav

    model_root = args.model_root
    onnx_root = args.onnx_root
    out = args.out

    profile = os.path.join(out, "voice_profile")
    runtime = os.path.join(out, "runtime_input")
    fixture = os.path.join(out, "flow_fixture")
    for d in (profile, runtime, fixture):
        os.makedirs(d, exist_ok=True)

    print("=" * 66)
    print("  CosyVoice2 音色注册")
    print("=" * 66)
    print("  参考音频 : %s" % args.wav)
    print("  prompt   : %s" % args.prompt_wav)
    print("  转写文字 : %s" % text)
    print()

    # ── 文字 token ──
    print("[1/5] 文本 BPE 分词")
    tk = BpeTokenizer(os.path.join(model_root, "text_frontend", "tokenizer.bin"))
    text_ids = np.array(tk.tokenize(text), dtype=np.int32)
    print("      转写文字 → %d 个 token" % len(text_ids))

    # ── 说话人向量 ──
    print("[2/5] CampPlus 说话人向量")
    fe = FeatureExtractor(os.path.join(onnx_root, "models/prompt/campplus.onnx"),
                          os.path.join(onnx_root, "models/prompt/speech_tokenizer_v2.static_seq1753.onnx"))
    wav16 = load_mono(args.wav, 16000)
    emb = fe.speaker_embedding(wav16)
    print("      参考音频 %.2f s → embedding %d 维" % (len(wav16) / 16000.0, len(emb)))

    w_aff = np.fromfile(os.path.join(model_root, "spk_affine_weight.f32.bin"),
                        dtype=np.float32).reshape(80, 192)
    b_aff = np.fromfile(os.path.join(model_root, "spk_affine_bias.f32.bin"),
                        dtype=np.float32)
    spks = (w_aff @ emb + b_aff).astype(np.float32)

    # ⚠️ 必须做 L2 归一化！
    #    flow 模型期望的说话人向量是单位范数的（参考 spks 范围 ±0.31、范数 0.82）。
    #    不做这一步的话量级会大 18 倍（范数 ~14.7），
    #    flow 输出会被压成静音（实测 RMS 只有 -43.7 dBFS，比正常低 25 dB）。
    spks = spks / max(float(np.linalg.norm(spks)), 1e-9)
    print("      spk_affine → spks %d 维（已归一化，范数 %.4f）"
          % (len(spks), float(np.linalg.norm(spks))))

    # ── prompt 片段：语音 token + mel ──
    # ⚠️ speech tokenizer 是固定 1753 帧（17.53 秒）的静态模型，
    #    所以音频必须裁到这个长度，不能靠补零（注意力会被稀释）。
    print("[3/5] prompt 特征")
    pwav16 = fit_length(load_mono(args.prompt_wav, 16000), TOKENIZER_SAMPLES_16K)
    pwav24 = fit_length(load_mono(args.prompt_wav, 24000), TOKENIZER_SAMPLES_24K)
    raw_tokens = fe.speech_tokens(pwav16)
    mel = mel24(pwav24)
    aligned = min(len(mel) // 2, len(raw_tokens))
    tokens = raw_tokens[:aligned]
    print("      prompt %.2f s → 原始 token %d 个，对齐后 %d 个 | mel %d 帧"
          % (len(pwav16) / 16000.0, len(raw_tokens), len(tokens), len(mel)))

    # LLM prompt 用前 3 秒（25 token/秒）；flow prompt 用末 2 秒
    llm_tokens = tokens[:75]
    flow_tokens = tokens[-50:]
    flow_mel = mel[-100:]
    print("      LLM prompt %d token | flow prompt %d token + %d 帧 mel"
          % (len(llm_tokens), len(flow_tokens), len(flow_mel)))

    # ── 落盘 ──
    print("[4/5] 写出资产")

    def w_i32(path, arr):
        np.ascontiguousarray(arr, dtype=np.int32).tofile(path)

    def w_f32(path, arr):
        np.ascontiguousarray(arr, dtype=np.float32).tofile(path)

    with open(os.path.join(profile, "prompt_text.txt"), "w", encoding="utf-8") as f:
        f.write(text + "\n")
    w_i32(os.path.join(profile, "prompt_text_token.i32.bin"), text_ids)
    w_i32(os.path.join(profile, "llm_prompt_speech_token.i32.bin"), llm_tokens)
    w_i32(os.path.join(runtime, "flow_prompt_token_50.i32.bin"), flow_tokens)
    w_f32(os.path.join(runtime, "prompt_feat_100.f32.bin"), flow_mel.reshape(-1))
    w_f32(os.path.join(fixture, "spks.f32.bin"), spks)

    for p in (os.path.join(profile, "prompt_text_token.i32.bin"),
              os.path.join(profile, "llm_prompt_speech_token.i32.bin"),
              os.path.join(runtime, "flow_prompt_token_50.i32.bin"),
              os.path.join(runtime, "prompt_feat_100.f32.bin"),
              os.path.join(fixture, "spks.f32.bin")):
        print("      %-58s %d 字节" % (os.path.relpath(p, out), os.path.getsize(p)))

    print("[5/5] 完成")
    print()
    print("=" * 66)
    print("  用法：")
    print("    bash rknn/scripts/run_cosyvoice2_streaming_rk3588.sh sample \\")
    print("      (设置 NEXTTTS_ROOT / PIPELINE_BIN / VOICE_PROFILE_ROOT /)")
    print("      (     RUNTIME_ROOT / FLOW_FIXTURE / TEXT 环境变量)")
    print("=" * 66)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
