#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""找出 ROCK 5B+ 上「KEY」按钮接在哪个 GPIO 上（带上拉版）。

────────────────────────────────────────────────────────
为什么需要「上拉」
────────────────────────────────────────────────────────
第一版扫描是直接读电平的，结果一堆线在 40 秒里跳了 20 多次 ——
因为 RK3588 的 GPIO 默认可能没有内部上下拉，**浮空引脚的电平会随机漂移**，
真实按键的变化被噪声淹没了。

所以这一版：**给每条候选线打开内部上拉（BIAS_PULL_UP）**，然后全程保持请求不释放。

    浮空线      → 被上拉稳定到 1，不再乱跳          ✅ 噪声消失
    按钮线      → 松开时被上拉为 1，按下接地变 0     ✅ 干净的一对跳变
    外部拉低的线 → 上拉也拉不起来，一直读 0          → 单独列出来

安全说明：
    * 不碰任何「已被驱动占用」的线
    * 只用内部上拉（约 50kΩ），不驱动、不输出任何电平
    * 全程只读值
"""

import glob
import sys
import time

import gpiod

BIAS_UP = getattr(gpiod, "LINE_REQ_FLAG_BIAS_PULL_UP", 0)


def chip_names():
    for dev in sorted(glob.glob("/dev/gpiochip*")):
        yield dev.rsplit("/", 1)[-1]


class Held(object):
    """同时持有所有候选线的请求（保持上拉生效）"""

    def __init__(self):
        self.entries = []   # (label, line)
        self._chips = []
        self.used = 0
        self.failed = 0
        self.zero_after_pullup = []

    def open(self):
        for chipname in chip_names():
            try:
                chip = gpiod.Chip(chipname)
            except Exception:
                continue
            self._chips.append(chip)
            for i in range(chip.num_lines()):
                line = chip.get_line(i)
                if line.is_used():
                    self.used += 1
                    continue
                try:
                    line.request(
                        consumer="findbtn",
                        type=gpiod.LINE_REQ_DIR_IN,
                        flags=BIAS_UP,
                    )
                except Exception:
                    self.failed += 1
                    continue
                label = "%s line %-3d %s" % (chipname, i, line.name() or "-")
                self.entries.append((label, line))

        # 上拉打开后先稳定一下再取基线
        time.sleep(0.5)
        for label, line in self.entries:
            if line.get_value() == 0:
                self.zero_after_pullup.append(label)
        return self

    def read(self):
        return {label: line.get_value() for label, line in self.entries}

    def close(self):
        for _, line in self.entries:
            try:
                line.release()
            except Exception:
                pass
        for chip in self._chips:
            try:
                chip.close()
            except Exception:
                pass


def main():
    wait_rounds = int(sys.argv[1]) if len(sys.argv) > 1 else 40

    print("=" * 70)
    print("  扫描（内部上拉版）")
    print("  上拉标志: %s" % ("已启用" if BIAS_UP else "⚠️ 不可用，退化为直接读"))
    print("=" * 70)

    held = Held().open()
    print("  可读线 %d 条 | 被占用跳过 %d | 请求失败 %d"
          % (len(held.entries), held.used, held.failed))
    print("  打开上拉后仍读 0 的线 %d 条（这些是外部拉低的，不是按钮）："
          % len(held.zero_after_pullup))
    for lab in held.zero_after_pullup[:15]:
        print("      %s" % lab)
    if len(held.zero_after_pullup) > 15:
        print("      …还有 %d 条" % (len(held.zero_after_pullup) - 15))

    base = held.read()

    # 先静默观察 5 秒 —— 如果这 5 秒内就有线乱跳，说明它还在浮空
    print()
    print("  先静默观察 5 秒，检查还有没有浮空噪声…")
    noisy = {}
    last = dict(base)
    for _ in range(5):
        time.sleep(1.0)
        cur = held.read()
        for k in cur:
            if cur[k] != last[k]:
                noisy[k] = noisy.get(k, 0) + 1
        last = cur
    if noisy:
        print("  ⚠️  仍有 %d 条线在无人操作时乱跳：" % len(noisy))
        for k, n in sorted(noisy.items(), key=lambda kv: -kv[1])[:10]:
            print("        %-40s 跳了 %d 次" % (k, n))
    else:
        print("  ✅ 所有线都稳定，没有任何噪声")

    print()
    print("=" * 70)
    print("  ⚠️  现在请按住 KEY 按钮，保持 3 秒再松开，重复 6 次")
    print("     接下来 %d 秒内每秒采样一次" % wait_rounds)
    print("=" * 70)

    last = held.read()
    hits = {}
    for t in range(wait_rounds):
        time.sleep(1.0)
        cur = held.read()
        for k in cur:
            if cur[k] != last[k]:
                hits.setdefault(k, []).append((t + 1, last[k], cur[k]))
                print("  [%2ds]  %-40s  %d -> %d" % (t + 1, k, last[k], cur[k]))
        last = cur

    print()
    print("=" * 70)
    if hits:
        print("  电平变化统计：")
        print()
        for k, evs in sorted(hits.items(), key=lambda kv: -len(kv[1])):
            downs = sum(1 for _, a, b in evs if a == 1 and b == 0)
            ups = sum(1 for _, a, b in evs if a == 0 and b == 1)
            print("     %-40s 共 %2d 次（1→0: %d, 0→1: %d）"
                  % (k, len(evs), downs, ups))
        print()
        best = max(hits.items(), key=lambda kv: len(kv[1]))
        print("  🎯 最可能的 KEY 按钮： %s（变化 %d 次）" % (best[0], len(best[1])))
        print("     判据：1→0 和 0→1 的次数应该大致相等（按下 / 松开）")
    else:
        print("  ❌ 没有任何线变化 —— KEY 按钮不是接在普通 GPIO 上")
    print("=" * 70)

    held.close()


if __name__ == "__main__":
    main()
