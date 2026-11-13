#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""实时观察 USB 声卡的插拔状态 —— 用来验证检测逻辑到底有没有效

用法：
    ~/proj/venv/bin/python tools/watch_usb.py

然后动手把 USB 声卡拔掉、再插上，根据输出判断：

    18:52:01  USB 声卡 已插入     ← 程序启动时就是插着的
    18:52:14  USB 声卡 已拔出     ← 你拔掉的那一刻
    18:52:22  USB 声卡 已插入     ← 你插回的那一刻

如果拔掉后**没有**出现「已拔出」，说明检测方式不成立，得换方案。

原理和 app.py 里的 _usb_card_present() 完全一致：
遍历 /proc/asound/card*/usbid，看有没有匹配 VID:PID 的。
"""

import glob
import sys
import time

# C-Media USB 声卡（绿联 30724）。想测别的设备就改这里。
WANT = "0d8c:0014"


def card_present(want=WANT):
    """返回 (是否插着, 匹配到的 usbid 路径)"""
    want = want.strip().lower()
    if not want:
        return False, None
    for path in glob.glob("/proc/asound/card*/usbid"):
        try:
            with open(path, "r") as f:
                if f.read().strip().lower() == want:
                    return True, path
        except OSError:
            continue
    return False, None


def main():
    print("监视 USB 声卡 %s 的插拔（Ctrl+C 退出）" % WANT)
    print("现在动手拔/插声卡，看下面有没有反应\n")

    last = None
    start = time.time()
    while True:
        try:
            cur, path = card_present()
            if cur != last:
                stamp = time.strftime("%H:%M:%S")
                if cur:
                    print("%s  ✅ 已插入   (%s)" % (stamp, path), flush=True)
                else:
                    print("%s  ❌ 已拔出" % stamp, flush=True)
                last = cur
            time.sleep(0.3)
        except KeyboardInterrupt:
            print("\n退出。总共观察了 %.1f 秒" % (time.time() - start))
            return 0


if __name__ == "__main__":
    raise SystemExit(main())
