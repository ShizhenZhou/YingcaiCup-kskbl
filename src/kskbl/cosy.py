# -*- coding: utf-8 -*-
"""CosyVoice2 音色克隆封装 —— 把 C++ NPU 流水线包成一个函数

────────────────────────────────────────────────────────
它做的是什么
────────────────────────────────────────────────────────
    **文字 → 语音**（TTS），但用**指定参考音频的音色**。

    ⚠️ 这不是"语音→语音"的变声。想保留自己的语调和节奏，
       要用 src/kskbl/voice.py 里的 DSP 波形变声。

────────────────────────────────────────────────────────
依赖
────────────────────────────────────────────────────────
    板子上已部署的 CosyVoice2 项目（约 2.9 GB）：
        ~/proj/cosyvoice2_rknn/
            rknn/cpp/build/cosyvoice2_streaming_pipeline   编译好的引擎
            rknn/models/cosyvoice2/                        共享模型
            onnx/                                          ONNX 模型

    音色资产用 tools/cosy_register.py 生成，含 5 个文件：
        voice_profile/  prompt_text_token.i32.bin
                        llm_prompt_speech_token.i32.bin
        runtime_input/  flow_prompt_token_50.i32.bin
                        prompt_feat_100.f32.bin
        flow_fixture/   spks.f32.bin

────────────────────────────────────────────────────────
性能（RK3588 实测）
────────────────────────────────────────────────────────
    每次调用约 10~11 秒：
        模型加载 3.7 s + LLM 生成 ~3.5 s + flow ~1 s + HiFT ~1 s
    持续 RTF 约 0.78（比实时快），但每轮都要重新加载模型。
"""

import os
import subprocess
import time

# 默认部署路径
DEFAULT_ROOT = os.path.expanduser("~/proj/cosyvoice2_rknn")

# 音色资产目录（tools/cosy_register.py 的 --out 参数）
#
# ⚠️ 一定要放在**持久目录**，不能放 /tmp。
#    踩过坑：资产放在 /tmp 里，隔天再跑就报「曼波资产缺失」——
#    系统的 tmpfiles 清理把整个目录删了，音色就"消失"了。
DEFAULT_VOICES_ROOT = os.path.expanduser("~/proj/voices")

DEFAULT_PROFILES = {
    "manbo": {
        "profile": os.path.join(DEFAULT_VOICES_ROOT, "manbo", "voice_profile"),
        "runtime": os.path.join(DEFAULT_VOICES_ROOT, "manbo", "runtime_input"),
        "fixture": os.path.join(DEFAULT_VOICES_ROOT, "manbo", "flow_fixture"),
    },
}


class CosyVoice(object):
    """调 C++ 流水线做音色克隆 TTS。

    参数
        root     : CosyVoice 项目根目录（含 rknn/ 和 onnx/）
        profile  : 音色的 voice_profile 目录
        runtime  : 音色的 runtime_input 目录
        fixture  : 音色的 flow_fixture 目录
        out_dir  : 输出目录
        timeout  : 单次调用超时（秒）
    """

    def __init__(self, root=None, profile=None, runtime=None,
                 fixture=None, out_dir="/tmp/cosy_app", timeout=900):
        # ⚠️ 必须给 None 兜底：调用方传 root=None（比如 argparse 默认值）时
        #    os.path.expanduser(None) 会抛 TypeError，这里的默认路径就是为此留的。
        self.root = os.path.expanduser(root or DEFAULT_ROOT)
        self.rknn = os.path.join(self.root, "rknn")
        self.profile = profile
        self.runtime = runtime
        self.fixture = fixture
        self.out_dir = out_dir
        self.timeout = timeout

    @classmethod
    def from_preset(cls, name, root=DEFAULT_ROOT, out_dir=None):
        """用注册好的音色预设创建。目前只有 manbo。"""
        if name not in DEFAULT_PROFILES:
            raise KeyError("未知音色 %r，可用：%s"
                           % (name, ", ".join(sorted(DEFAULT_PROFILES))))
        cfg = DEFAULT_PROFILES[name]
        return cls(root=root,
                   profile=cfg["profile"],
                   runtime=cfg["runtime"],
                   fixture=cfg["fixture"],
                   out_dir=out_dir or ("/tmp/cosy_" + name))

    def check(self):
        """检查依赖是否齐全，返回缺失项列表（空列表=可用）"""
        missing = []
        binary = os.path.join(self.rknn, "cpp", "build",
                              "cosyvoice2_streaming_pipeline")
        script = os.path.join(self.rknn, "scripts",
                              "run_cosyvoice2_streaming_rk3588.sh")
        for p in (binary, script, self.profile, self.runtime, self.fixture):
            if p and not os.path.exists(p):
                missing.append(p)
        return missing

    def synthesize(self, text, out_dir=None, timeout=None, on_progress=None):
        """合成并返回 (wav_path, metrics)。

        on_progress(ratio)  可选的进度回调，ratio 0.0~1.0。
            进度是**按时间渐进**的估算（不是模型真实进度），
            越接近完成推进得越慢，最多到 0.95，
            这样不会出现"卡在 99%"或"进度回退"的观感。
        """
        if not text or not text.strip():
            raise ValueError("文本为空")

        out = out_dir or self.out_dir
        os.makedirs(out, exist_ok=True)

        text_path = os.path.join(out, "target.txt")
        with open(text_path, "w", encoding="utf-8") as f:
            f.write(text.strip())

        env = dict(os.environ)
        env.update({
            "NEXTTTS_ROOT": self.rknn,
            "PIPELINE_BIN": os.path.join(self.rknn, "cpp", "build",
                                         "cosyvoice2_streaming_pipeline"),
            "VOICE_PROFILE_ROOT": self.profile,
            "RUNTIME_ROOT": self.runtime,
            "FLOW_FIXTURE": self.fixture,
            "TEXT_FILE": text_path,
            "OUTPUT_ROOT": out,
            "PCM_STREAM": os.path.join(out, "audio.s16le"),
        })

        script = os.path.join(self.rknn, "scripts",
                              "run_cosyvoice2_streaming_rk3588.sh")
        limit = timeout or self.timeout
        t0 = time.time()

        proc = subprocess.Popen(["bash", script, "sample"], env=env,
                                stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True)
        import math

        while proc.poll() is None:
            elapsed = time.time() - t0
            if elapsed > limit:
                proc.kill()
                raise RuntimeError("CosyVoice 合成超时（%.0f 秒）" % limit)
            if on_progress is not None:
                # 渐进式：tau 秒后大约走到 63%，之后越来越慢，封顶 0.95
                on_progress(0.95 * (1.0 - math.exp(-elapsed / 12.0)))
            time.sleep(0.15)

        stdout, _ = proc.communicate()
        elapsed = time.time() - t0

        wav = os.path.join(out, "streaming_segment00.wav")
        if proc.returncode != 0 or not os.path.exists(wav):
            tail = "\n".join((stdout or "").splitlines()[-15:])
            raise RuntimeError("CosyVoice 合成失败（exit %d）\n%s"
                               % (proc.returncode, tail))

        metrics = {"elapsed": elapsed, "wav": wav}
        for line in (stdout or "").splitlines():
            k, sep, v = line.partition("=")
            if sep and k.strip().isidentifier():
                metrics[k.strip()] = v.strip()
        return wav, metrics
