#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""观察 3.5mm 耳机口的插入/拔出，确认用哪种方式检测最可靠。

────────────────────────────────────────────────────────
背景：ROCK 5B+ 的耳机口插入检测是怎么接的
────────────────────────────────────────────────────────
设备树里：

    analog-sound {
        compatible = "audio-graph-card";
        label = "rk3588-es8316";
        hp-det-gpios = <&gpio1 RK_PD5 GPIO_ACTIVE_HIGH>;
    };

对应的 GPIO 是 gpiochip1 第 29 线，名字 "hp-det"，
但**被声卡驱动占用了**（gpioinfo 显示 [used]），所以用户态不能直接 gpiod 读它。

不过驱动把它导出成了两个用户态可用的接口：

    ① 输入设备 /dev/input/event8  ("rockchip-es8316 Headset")
       B: EV=21  B: SW=14   → 会上报 SW_HEADPHONE_INSERT / SW_MICROPHONE_INSERT

    ② ALSA 控制
       numid=38  iface=CARD  name='Headphone Jack'   type=BOOLEAN access=r--
       numid=39  iface=CARD  name='Headset Mic Jack'
       用 amixer -c <card> cget numid=38 读，values=on/off

本脚本**同时盯着这两个**，插拔几次就能看出：
    * 事件有没有真的上报、时间戳对不对
    * ALSA 的状态跟事件是否一致
    * 两者谁更适合当触发源

用法：
    python3 find_jack.py                 # 默认看 event8 和 card 4
    python3 find_jack.py --event /dev/input/event8 --card 4
"""

import argparse
import os
import select
import struct
import subprocess
import sys
import time

# struct input_event { struct timeval time; __u16 type; __u16 code; __s32 value; }
EVENT_FMT = struct.Struct("llHHi")

EV_SYN = 0x00
EV_KEY = 0x01
EV_SW = 0x05

SW_NAMES = {
    0x00: "SW_LID",
    0x01: "SW_TABLET_MODE",
    0x02: "SW_HEADPHONE_INSERT",
    0x03: "SW_RFKILL_ALL",
    0x04: "SW_MICROPHONE_INSERT",
    0x05: "SW_DOCK",
    0x06: "SW_LINEOUT_INSERT",
    0x07: "SW_JACK_PHYSICAL_INSERT",
    0x08: "SW_VIDEOOUT_INSERT",
    0x09: "SW_CAMERA_LENS_COVER",
    0x0A: "SW_KEYPAD_SLIDE",
    0x0B: "SW_FRONT_PROXIMITY",
    0x0C: "SW_ROTATE_LOCK",
    0x0D: "SW_LINEIN_INSERT",
}

JACK_NUMIDS = [
    (38, "Headphone Jack"),
    (39, "Headset Mic Jack"),
]


def read_alsa_jack(card):
    """返回 {'Headphone Jack': 'on'/'off'/'?', ...}"""
    result = {}
    for numid, name in JACK_NUMIDS:
        try:
            out = subprocess.run(
                ["amixer", "-c", str(card), "cget", "numid=%d" % numid],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                timeout=2.0,
            ).stdout.decode("utf-8", "replace")
        except Exception as exc:
            result[name] = "ERR(%s)" % exc
            continue
        value = "?"
        for line in out.splitlines():
            line = line.strip()
            if line.startswith(": values="):
                value = line.split("=", 1)[1].strip()
        result[name] = value
    return result


def main():
    ap = argparse.ArgumentParser(description="观察耳机口插入检测")
    ap.add_argument("--event", default="/dev/input/event8", help="输入设备节点")
    ap.add_argument("--card", type=int, default=4, help="声卡号（默认 4）")
    ap.add_argument("--no-alsa", action="store_true", help="不轮询 ALSA 状态")
    args = ap.parse_args()

    print("=" * 68)
    print("  耳机插孔检测观察")
    print("=" * 68)
    print("  输入设备 : %s" % args.event)
    print("  声卡     : card %d" % args.card)

    if not os.path.exists(args.event):
        print("\n❌ 找不到 %s" % args.event)
        return 1
    if not os.access(args.event, os.R_OK):
        print("\n❌ 没有权限读 %s" % args.event)
        print("   需要： sudo usermod -aG input radxa  然后重新登录")
        return 1

    try:
        out = subprocess.run(
            ["amixer", "-c", str(args.card), "cget", "numid=38"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=2.0
        ).stdout.decode("utf-8", "replace")
        print("  ALSA 控制: 可读 ✅")
    except Exception as exc:
        print("  ALSA 控制: 读取失败 (%s)" % exc)

    fd = os.open(args.event, os.O_RDONLY)
    buf = b""
    t0 = time.monotonic()
    last_alsa_poll = 0.0
    last_alsa_state = None

    print()
    print("=" * 68)
    print("  ⚠️  现在请把 3.5mm 插头插入耳机口，停 2 秒再拔出，重复 3 次")
    print("     Ctrl+C 退出")
    print("=" * 68)
    print()

    try:
        while True:
            ready, _, _ = select.select([fd], [], [], 0.25)

            if ready:
                buf += os.read(fd, EVENT_FMT.size * 32)
                while len(buf) >= EVENT_FMT.size:
                    _, _, etype, code, value = EVENT_FMT.unpack_from(buf, 0)
                    buf = buf[EVENT_FMT.size:]
                    if etype == EV_SYN:
                        continue
                    if etype == EV_SW:
                        name = SW_NAMES.get(code, "SW_?=%d" % code)
                        action = "插入 ✅" if value else "拔出 ❌"
                        print("  [%6.2fs]  SW 事件   %-24s value=%d  → %s"
                              % (time.monotonic() - t0, name, value, action))
                    else:
                        print("  [%6.2fs]  其它事件  type=0x%02X code=%d value=%d"
                              % (time.monotonic() - t0, etype, code, value))

            if not args.no_alsa:
                now = time.monotonic()
                if now - last_alsa_poll >= 1.0:
                    last_alsa_poll = now
                    state = read_alsa_jack(args.card)
                    if state != last_alsa_state:
                        last_alsa_state = state
                        pairs = "  ".join("%s=%s" % (k, v) for k, v in state.items())
                        print("  [%6.2fs]  ALSA 状态  %s" % (now - t0, pairs))

    except KeyboardInterrupt:
        print("\n  已退出")
    finally:
        os.close(fd)

    print()
    print("=" * 68)
    print("  结论怎么看：")
    print("    * 事件有上报 → 方案 B（读 /dev/input/event8）可行，事件驱动、零延迟")
    print("    * ALSA 状态跟着 on/off → 方案 A（轮询 amixer）可行，能给绝对状态")
    print("    * 两者都行的话，推荐：事件驱动触发 + 启动时读一次 ALSA 对齐初值")
    print("=" * 68)
    return 0


if __name__ == "__main__":
    sys.exit(main())
