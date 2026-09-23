#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Foo —— 一个玩笑程序（不是正式项目代码）

────────────────────────────────────────────────────────
⚠️ 这是什么
────────────────────────────────────────────────────────
    这是做着玩的小玩具：插上 3.5mm 耳机插头，板子就放歌 + 屏幕换图。

    **它不属于「会听会变声的智能终端」的正式交付内容。**
    正式程序是另一套东西（变声处理 + 频谱显示），不要把这个文件编进去。

    它存在的意义主要是：
      1. 好玩
      2. 顺便验证了「耳机插入检测」这条硬件通路是通的（含详细注释）

────────────────────────────────────────────────────────
行为
────────────────────────────────────────────────────────
    启动                →  屏幕显示开屏画面
    插入 3.5mm 插头     →  播放音乐 +（同一瞬间）屏幕切到图片
    拔出插头            →  停止播放，回到开屏
    音乐自然放完        →  自动回到开屏
                            （插头还插着也不会自动重播，要拔出再插）

────────────────────────────────────────────────────────
耳机插入检测是怎么做的（2026-09-23 实机确认）
────────────────────────────────────────────────────────
设备树里：

    analog-sound {
        compatible = "audio-graph-card";
        label = "rk3588-es8316";
        hp-det-gpios = <&gpio1 RK_PD5 GPIO_ACTIVE_HIGH>;
    };

对应 gpiochip1 第 29 线，名字 "hp-det"，但**被声卡驱动占用了**
（gpioinfo 显示 [used]），用户态不能直接 gpiod 读。

不过驱动把它导出成两个用户态接口，**两个实测都能用**：

    ① /dev/input/event8   ("rockchip-es8316 Headset")
       上报 EV_SW / SW_HEADPHONE_INSERT：value=1 插入，value=0 拔出
       → 本程序用它做**事件触发**（零延迟、不轮询）

    ② ALSA  numid=38  iface=CARD  name='Headphone Jack'   (BOOLEAN, 只读)
       amixer -c 4 cget numid=38  →  values=on / off
       → 本程序用它读**绝对状态**（事件只知道"变了"，不知道"当前是什么"）

实机实测（插拔 3 次 + 最后插回）：4 次插入 / 3 次拔出，与操作完全一致。

⚠️ 权限：读 /dev/input/event8 需要 radxa 在 input 组里
       sudo usermod -aG input radxa    然后重新登录

────────────────────────────────────────────────────────
用法
────────────────────────────────────────────────────────
    python3 -u foo.py                       # 直接跑
    python3 -u foo.py --test-jack           # 只观察插拔，不碰屏幕和音频
    python3 -u foo.py --help

    python3 -u foo.py --mp3 /path/song.mp3 --image /path/photo.png
"""

import argparse
import os
import select
import struct
import subprocess
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from kskbl.st7789 import ST7789  # noqa: E402

# 复用点亮验证脚本里已经调好的开屏设计 / 图片缩放逻辑
from screen_bringup import make_image, make_text  # noqa: E402

DEFAULT_MP3 = "/home/radxa/Music/Wiz Khalifa,Charlie Puth - See You Again_01.mp3"
DEFAULT_IMAGE = "/home/radxa/Desktop/kobe.png"

# 耳机插入检测
JACK_EVENT = "/dev/input/event8"
JACK_CARD = 4
JACK_NUMID = 38          # Headphone Jack

# ── Linux input 子系统常量 ────────────────────────────────────
EV_SW = 0x05
SW_HEADPHONE_INSERT = 0x02
# struct input_event { struct timeval time; __u16 type; __u16 code; __s32 value; }
EVENT_FMT = struct.Struct("llHHi")


# ── 耳机插孔监听 ─────────────────────────────────────────────
class JackWatcher(object):
    """3.5mm 耳机口插入检测。

    * 事件源：/dev/input/event8 的 SW_HEADPHONE_INSERT 开关事件（事件驱动）
    * 初值对齐：启动时用 ALSA numid=38 读一次绝对状态
    """

    def __init__(self, event=JACK_EVENT, card=JACK_CARD, code=SW_HEADPHONE_INSERT):
        self.event = event
        self.card = card
        self.code = code
        self._buf = b""

        if not os.path.exists(event):
            raise SystemExit("\n❌ 找不到 %s\n" % event)
        try:
            self.fd = os.open(event, os.O_RDONLY)
        except PermissionError:
            raise SystemExit(
                "\n❌ 没有权限读 %s\n"
                "   执行： sudo usermod -aG input radxa\n"
                "   然后重新登录（重开一个 SSH 会话）再试。\n" % event
            )

    def current_state(self):
        """读 ALSA 插孔状态：True=已插入，False=未插入，None=读不到"""
        try:
            out = subprocess.run(
                ["amixer", "-c", str(self.card), "cget", "numid=%d" % JACK_NUMID],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                timeout=2.0,
            ).stdout.decode("utf-8", "replace")
        except Exception:
            return None
        for line in out.splitlines():
            line = line.strip()
            if line.startswith(": values="):
                return line.split("=", 1)[1].strip() == "on"
        return None

    def wait(self, timeout):
        """最多等 timeout 秒；返回 'insert' / 'remove' / None"""
        deadline = time.monotonic() + timeout
        while True:
            remain = deadline - time.monotonic()
            if remain <= 0:
                return None
            ready, _, _ = select.select([self.fd], [], [], remain)
            if not ready:
                return None

            self._buf += os.read(self.fd, EVENT_FMT.size * 32)
            result = None
            while len(self._buf) >= EVENT_FMT.size:
                _, _, etype, code, value = EVENT_FMT.unpack_from(self._buf, 0)
                self._buf = self._buf[EVENT_FMT.size:]
                if etype == EV_SW and code == self.code:
                    result = "insert" if value else "remove"
            if result:
                return result

    def close(self):
        try:
            os.close(self.fd)
        except OSError:
            pass


# ── 音频播放 ─────────────────────────────────────────────────
class Mp3Player(object):
    """用 VLC 的命令行版本播放 mp3。

    注：/usr/bin/cvlc 是个 shell 包装脚本，但它用的是 exec
        —— 会用 vlc 替换掉自己，所以 terminate 就是直接杀 vlc。
    """

    def __init__(self, path):
        self.path = path
        self.proc = None

    def start(self):
        cmd = [
            "cvlc",
            "--intf", "dummy",     # 不要图形界面
            "--play-and-exit",     # 放完自动退出
            "--quiet",
            self.path,
        ]
        self.proc = subprocess.Popen(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return self.proc

    def is_playing(self):
        return self.proc is not None and self.proc.poll() is None

    def stop(self):
        if self.proc is None:
            return
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                try:
                    self.proc.wait(timeout=1.0)
                except subprocess.TimeoutExpired:
                    pass
        self.proc = None


def test_jack(event, card):
    """只观察插拔：不初始化屏幕、不放音乐"""
    print("=" * 62)
    print("  耳机插孔测试")
    print("=" * 62)
    print("  事件设备: %s" % event)
    print("  声卡    : card %d" % card)

    jack = JackWatcher(event=event, card=card)
    state = jack.current_state()
    if state is None:
        print("  当前状态: 读不到 ALSA（不影响事件检测）")
    else:
        print("  当前状态: %s" % ("已插入 🎧" if state else "未插入"))

    print("\n  已就绪，请插拔 3.5mm 插头。Ctrl+C 退出\n")

    t0 = time.monotonic()
    inserts = removes = 0
    try:
        while True:
            ev = jack.wait(1.0)
            if ev == "insert":
                inserts += 1
                print("  [%6.2fs]  插入 🎧  (第 %d 次)" % (time.monotonic() - t0, inserts))
            elif ev == "remove":
                removes += 1
                print("  [%6.2fs]  拔出 ❌  (第 %d 次)" % (time.monotonic() - t0, removes))
    except KeyboardInterrupt:
        print("\n  共检测到：插入 %d 次 / 拔出 %d 次" % (inserts, removes))
    finally:
        jack.close()
        print("  已释放")


# ── 主流程 ───────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser(
        description="Foo —— 插耳机就放歌的玩笑程序（不属于正式项目）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--mp3", default=DEFAULT_MP3, help="要播放的 mp3")
    ap.add_argument("--image", default=DEFAULT_IMAGE, help="播放时显示的图片")
    ap.add_argument("--event", default=JACK_EVENT, help="耳机插孔输入事件设备")
    ap.add_argument("--card", type=int, default=JACK_CARD, help="声卡号（读 ALSA 插孔状态用）")
    ap.add_argument("--speed", type=int, default=30_000_000, help="SPI 时钟 Hz（默认 30MHz）")
    ap.add_argument("--test-jack", action="store_true", help="只测插拔，不初始化屏幕和音频")
    args = ap.parse_args()

    if args.test_jack:
        test_jack(args.event, args.card)
        return

    for path, what in ((args.mp3, "音乐"), (args.image, "图片")):
        if not os.path.exists(path):
            raise SystemExit("\n❌ 找不到%s文件：%s\n" % (what, path))

    print("=" * 62)
    print("  Foo —— 插耳机就放歌")
    print("=" * 62)
    print("  音乐 : %s" % os.path.basename(args.mp3))
    print("  图片 : %s" % os.path.basename(args.image))
    print("  插孔 : %s (card %d)" % (args.event, args.card))
    print("  SPI  : %.1f MHz" % (args.speed / 1e6))

    print("\n[1/3] 打开耳机插孔检测")
    jack = JackWatcher(event=args.event, card=args.card)
    init_state = jack.current_state()
    if init_state is None:
        print("  就绪（当前插孔状态读不到）")
    else:
        print("  就绪，当前插孔状态：%s" % ("已插入 🎧" if init_state else "未插入"))

    print("\n[2/3] 初始化屏幕")
    lcd = ST7789(speed_hz=args.speed, width=240, height=240)
    lcd.init()
    print("  初始化完成")

    print("\n[3/3] 准备画面")
    t0 = time.perf_counter()
    splash_buf = make_text(lcd.width, lcd.height)
    photo_buf = make_image(args.image, lcd.width, lcd.height, fit="contain")
    print("  开屏与图片已渲染完成，耗时 %.0f ms" % ((time.perf_counter() - t0) * 1000))

    player = Mp3Player(args.mp3)
    state = "idle"

    try:
        lcd.show(splash_buf)
        print("\n" + "=" * 62)
        print("  ✅ 就绪 —— 现在屏幕上是开屏画面")
        print("     插入 3.5mm 插头开始播放；拔出即停止")
        print("     Ctrl+C 退出")
        print("=" * 62)

        while True:
            ev = jack.wait(0.2)

            if ev == "insert":
                if state == "idle":
                    # 先切画面再起播放 —— 视觉反馈立刻就有，不等 VLC 启动
                    lcd.show(photo_buf)
                    player.start()
                    state = "playing"
                    print("🎧 检测到插入 → 开始播放，屏幕已切到图片")
                else:
                    print("🎧 检测到插入（正在播放中，保持不动）")

            elif ev == "remove":
                if state == "playing":
                    player.stop()
                    lcd.show(splash_buf)
                    state = "idle"
                    print("🎧 检测到拔出 → 停止播放，回到开屏")
                else:
                    print("🎧 检测到拔出（本来就空闲，忽略）")

            elif state == "playing" and not player.is_playing():
                lcd.show(splash_buf)
                state = "idle"
                print("⏹  播放结束，回到开屏（要重播请拔出再插入）")

    except KeyboardInterrupt:
        print("\n\n收到 Ctrl+C，退出中……")
    finally:
        player.stop()
        jack.close()
        lcd.close()
        print("  资源已释放")


if __name__ == "__main__":
    main()
