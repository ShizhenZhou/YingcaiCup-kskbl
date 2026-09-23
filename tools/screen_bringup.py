#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ST7789 1.54" 240x240 SPI 屏 —— 点亮验证脚本

按「纯色 → 彩条 → 渐变 → 文字」的顺序逐级验证，
每一步都能单独定位问题，不用一次猜到底。

────────────────────────────────────────────────────────
用法
────────────────────────────────────────────────────────
    python3 screen_bringup.py                    # 依次跑全部 4 个测试
    python3 screen_bringup.py --mode solid       # 只跑纯色
    python3 screen_bringup.py --mode bars        # 只跑彩条
    python3 screen_bringup.py --mode gradient    # 只跑渐变
    python3 screen_bringup.py --mode text        # 只跑文字（带四角定位块）

    # 显示一张图片（默认 cover：等比放大盖满屏幕，居中裁边）
    python3 screen_bringup.py --mode image --image ~/Desktop/kobe.png

    # 不想被裁就用 contain（完整放入，四周留黑边）
    python3 screen_bringup.py --mode image --image ~/Desktop/kobe.png --fit contain

    # 花屏 / 雪花时降速重试
    python3 screen_bringup.py --speed 4000000

    # 画面整体偏移（比如整体往下错 80 行）时调这个
    python3 screen_bringup.py --mode text --rowstart 80

    # 颜色红蓝互换时翻转 RGB/BGR
    python3 screen_bringup.py --mode bars --bgr

────────────────────────────────────────────────────────
判定标准
────────────────────────────────────────────────────────
    solid     : 红→绿→蓝→白→黑，五个颜色都纯正、满屏
    bars      : 8 条竖彩条 + 白色细边框；红条必须是红的（不是蓝的）
    gradient  : 从左上(红)过渡到右下(蓝)，平滑无断层
    text      : 四角有 红/绿/蓝/白 四个小方块，中间有中文
                四角方块缺一个 → 画面偏移，调 --rowstart / --colstart
    image     : 显示指定图片。cover 满屏 / contain 留黑边，都是等比缩放
"""

import argparse
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from kskbl.st7789 import (  # noqa: E402
    MADCTL_BGR,
    MADCTL_MX,
    MADCTL_MY,
    ST7789,
    rgb565,
)

# (字体路径, ttc 内的索引) —— 按优先级排列
#
# ⚠️ 踩过的坑：DroidSansFallbackFull.ttf 是「中日韩补充字体」，**不含拉丁字母**，
#    用它渲染会把英文全变成方块。必须用 Noto Sans CJK 这种「中英文都全」的字体。
#    NotoSansCJK-Regular.ttc 的索引：0=JP 1=KR 2=SC 3=TC 4=HK
FONT_CANDIDATES = [
    ("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc", 2),  # Noto Sans CJK SC ✅
    ("/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc", 2),     # 粗体备选
    ("/usr/share/fonts/truetype/wqy/wqy-microhei.ttc", 0),          # 文泉驿微米黑
    ("/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc", 0),            # 文泉驿正黑
    ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 0),         # 纯拉丁兜底
]


def pick_font():
    """挑一个同时支持中文和英文的字体，返回 (路径, ttc索引)；找不到返回 (None, 0)"""
    from PIL import ImageFont

    for path, index in FONT_CANDIDATES:
        if not os.path.exists(path):
            continue
        try:
            ImageFont.truetype(path, 20, index=index)
            return path, index
        except Exception:
            continue
    return None, 0


def load_font(path, index, size):
    """按 (路径, 索引, 字号) 打开字体，path 为 None 时返回 None"""
    if path is None:
        return None
    from PIL import ImageFont

    return ImageFont.truetype(path, size, index=index)

# 标准彩条：(白 黄 青 绿 品红 红 蓝 黑)
BAR_COLORS = [
    (255, 255, 255),
    (255, 255, 0),
    (0, 255, 255),
    (0, 255, 0),
    (255, 0, 255),
    (255, 0, 0),
    (0, 0, 255),
    (0, 0, 0),
]


def banner(text):
    print("\n" + "=" * 56)
    print("  " + text)
    print("=" * 56)


def push(lcd, buf, label):
    """推一帧并报告耗时 —— 这个数字就是后面跑频谱的帧率上限"""
    t0 = time.perf_counter()
    lcd.show(buf)
    dt = time.perf_counter() - t0
    fps = (1.0 / dt) if dt > 0 else 0.0
    print("  %-18s 推帧 %6.1f ms  →  %5.1f fps" % (label, dt * 1000, fps))
    return dt


# ── 测试 1：纯色 ─────────────────────────────────────────────
def test_solid(lcd, hold_sec=0.9):
    banner("测试 1/4  纯色填充  （验供电 + 初始化 + 全屏寻址）")
    for name, color in [
        ("红 RED", (255, 0, 0)),
        ("绿 GREEN", (0, 255, 0)),
        ("蓝 BLUE", (0, 0, 255)),
        ("白 WHITE", (255, 255, 255)),
        ("黑 BLACK", (0, 0, 0)),
    ]:
        push(lcd, rgb565(*color).to_bytes(2, "big") * (lcd.width * lcd.height), name)
        time.sleep(hold_sec)
    print("\n  ✅ 五个颜色都纯正满屏 = 供电、初始化、寻址全部正常")


# ── 测试 2：彩条 ─────────────────────────────────────────────
def make_bars(width, height, border=True):
    import numpy as np

    n = len(BAR_COLORS)
    arr = np.zeros((height, width, 3), dtype=np.uint16)
    bar_w = width // n
    for i, (r, g, b) in enumerate(BAR_COLORS):
        x0 = i * bar_w
        x1 = width if i == n - 1 else (i + 1) * bar_w
        arr[:, x0:x1, 0] = r
        arr[:, x0:x1, 1] = g
        arr[:, x0:x1, 2] = b
    if border:
        arr[0:2, :, :] = 255          # 上下边框白
        arr[height - 2:height, :, :] = 255
        arr[:, 0:2, :] = 255          # 左右边框白
        arr[:, width - 2:width, :] = 255
    from kskbl.st7789 import rgb_array_to_565_bytes

    return rgb_array_to_565_bytes(arr)


def test_bars(lcd):
    banner("测试 2/4  彩条  （验颜色顺序 + 全屏覆盖）")
    print("  期望顺序：白 黄 青 绿 品红 红 蓝 黑，四周有一圈白边")
    push(lcd, make_bars(lcd.width, lcd.height), "8 色竖条+白边框")
    print("\n  → 红条显示成蓝色 = R/B 反了，加 --bgr 重跑")
    print("  → 白边只看到三条 = 画面偏移，调 --rowstart / --colstart")


# ── 测试 3：渐变 ─────────────────────────────────────────────
def make_gradient(width, height):
    import numpy as np

    x = np.linspace(0, 255, width, dtype=np.uint16)[None, :]
    y = np.linspace(0, 255, height, dtype=np.uint16)[:, None]
    arr = np.zeros((height, width, 3), dtype=np.uint16)
    arr[..., 0] = np.broadcast_to(x, (height, width))            # 横向 红→ ?
    arr[..., 1] = np.broadcast_to(y, (height, width))            # 纵向 暗→亮
    arr[..., 2] = np.broadcast_to(255 - x, (height, width))      # 横向 ? → 蓝
    from kskbl.st7789 import rgb_array_to_565_bytes

    return rgb_array_to_565_bytes(arr)


def test_gradient(lcd):
    banner("测试 3/4  渐变  （验 16bit 色彩 + 无断层）")
    print("  期望：左上角偏红 → 右下角偏蓝，纵向越来越亮，过渡平滑")
    push(lcd, make_gradient(lcd.width, lcd.height), "双轴渐变")
    print("\n  → 出现明显色带（阶梯）= 颜色位深丢了，可能 COLMOD 参数不对")
    print("  → 过渡平滑无断层 = RGB565 数据通路完全正常")


# ── 测试 4：文字 + 四角定位块 ─────────────────────────────────
def make_text(width, height, speed_hz=None):
    from PIL import Image, ImageDraw, ImageFont

    from kskbl.st7789 import image_to_565_bytes

    img = Image.new("RGB", (width, height), (8, 8, 24))
    d = ImageDraw.Draw(img)

    # 外边框
    d.rectangle([0, 0, width - 1, height - 1], outline=(90, 90, 110), width=1)

    # 四角定位块 —— 判断画面有没有偏移、方向对不对
    corners = [
        ((2, 2, 20, 20), (255, 0, 0), "左上=红"),
        ((width - 21, 2, width - 3, 20), (0, 255, 0), "右上=绿"),
        ((2, height - 21, 20, height - 3), (0, 0, 255), "左下=蓝"),
        ((width - 21, height - 21, width - 3, height - 3), (255, 255, 255), "右下=白"),
    ]
    for box, color, _ in corners:
        d.rectangle(box, fill=color)

    # 找字体（优先 Noto Sans CJK SC —— 中英文都全）
    font_path, font_index = pick_font()
    if font_path is None:
        print("  ⚠️  没找到可用字体，中文和英文都可能显示成方块")
    else:
        print("  使用字体：%s (index=%d)" % (os.path.basename(font_path), font_index))
    f_big = load_font(font_path, font_index, 34)
    f_mid = load_font(font_path, font_index, 20)
    f_sml = load_font(font_path, font_index, 15)

    def center(text, y, font, fill):
        if font is None:
            return
        w = d.textlength(text, font=font)
        d.text(((width - w) / 2, y), text, font=font, fill=fill)

    center("屏幕 OK", 58, f_big, (0, 255, 120))
    center("ST7789  240x240", 104, f_mid, (120, 200, 255))
    if speed_hz:
        # 把当前 SPI 速率画在屏上 —— 做速率扫描时一眼就能看出现在跑到多少
        center("SPI %.1f MHz" % (speed_hz / 1e6), 130, f_mid, (255, 240, 120))
    else:
        center("ROCK 5B+  SPI0", 130, f_mid, (200, 200, 200))
    center("会听会变声的智能终端", 186, f_sml, (255, 200, 80))

    # 底部一条假频谱，预告一下最终效果
    import numpy as np

    rng = np.random.RandomState(7)
    n_bars = 16
    bw = width // n_bars
    heights = rng.randint(6, 30, n_bars)
    for i, bh in enumerate(heights):
        x0 = i * bw + 2
        x1 = x0 + bw - 4
        y0 = height - 6 - bh
        ratio = i / float(n_bars - 1)
        color = (int(60 + 195 * ratio), int(120 * (1 - ratio)), int(255 * (1 - ratio)))
        d.rectangle([x0, y0, x1, height - 6], fill=color)

    return image_to_565_bytes(img)


def test_text(lcd, speed_hz=None):
    banner("测试 4/4  文字 + 四角定位块  （验偏移 + 中文字体）")
    print("  期望：四角各有一个小方块（左上红 右上绿 左下蓝 右下白）")
    print("        中间是「屏幕 OK / ST7789 / SPI 速率」，底部一条假频谱")
    push(lcd, make_text(lcd.width, lcd.height, speed_hz), "文字+四角+假频谱")
    print("\n  → 四个角块都在 = 寻址偏移正确，可以进入下一步")
    print("  → 缺了上面两个 = 画面往下偏，试 --rowstart 80")
    print("  → 缺了下面两个 = 画面往上偏，试 --rowstart -80")
    print("  → 中文变方块 = 字体不含中文字形（换成 Noto Sans CJK）")
    print("  → 英文变方块 = 字体不含拉丁字形（别用 DroidSansFallback）")


# ── 测试 5：图片显示 ─────────────────────────────────────────
def make_image(path, width, height, fit="cover"):
    """把任意图片缩放/裁切成屏幕尺寸，返回 RGB565 缓冲。

    fit="cover"   : 等比放大到盖满屏幕，多出来的部分居中裁掉。
                    照片首选 —— 满屏无黑边，代价是裁掉一点边缘。
    fit="contain" : 等比缩小到完整放进屏幕，四周留黑边。
                    不裁掉任何内容，代价是画面变小、有黑边。

    ⚠️ 两种都是**等比**缩放，绝不会把人拉扁或拉长。
    """
    from PIL import Image

    from kskbl.st7789 import image_to_565_bytes

    img = Image.open(path).convert("RGB")
    src_w, src_h = img.size

    if fit == "cover":
        scale = max(width / src_w, height / src_h)
    else:
        scale = min(width / src_w, height / src_h)
    new_w = max(1, int(round(src_w * scale)))
    new_h = max(1, int(round(src_h * scale)))
    img = img.resize((new_w, new_h), Image.LANCZOS)

    if fit == "cover":
        left = (new_w - width) // 2
        top = (new_h - height) // 2
        img = img.crop((left, top, left + width, top + height))
        note = "居中裁掉多余部分"
    else:
        canvas = Image.new("RGB", (width, height), (12, 12, 12))
        canvas.paste(img, ((width - new_w) // 2, (height - new_h) // 2))
        img = canvas
        note = "完整放入，四周留黑边"

    print("  原图 %d x %d  →  等比缩放到 %d x %d  →  %s，%s"
          % (src_w, src_h, new_w, new_h, fit, note))
    return image_to_565_bytes(img)


def test_image(lcd, path, fit="cover"):
    banner("图片显示")
    if not os.path.exists(path):
        print("  ❌ 找不到文件：%s" % path)
        return
    print("  文件：%s" % path)
    push(lcd, make_image(path, lcd.width, lcd.height, fit), "图片 (%s)" % fit)
    print("\n  → 人被拉扁/拉长 = 绝对不该发生（这里都是等比缩放）")
    print("  → 头顶被切掉一截 = 改用 --fit contain 看完整图")


# ── 主流程 ───────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser(
        description="ST7789 240x240 屏点亮验证",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument(
        "--mode",
        default="all",
        choices=["all", "solid", "bars", "gradient", "text", "image"],
        help="跑哪个测试（默认 all）",
    )
    ap.add_argument("--image", default=None, help="--mode image 时要显示的图片路径")
    ap.add_argument(
        "--fit", default="cover", choices=["cover", "contain"],
        help="图片缩放方式：cover = 满屏裁边（默认），contain = 完整放入留黑边",
    )
    ap.add_argument(
        "--speed", type=int, default=10_000_000,
        help="SPI 时钟 Hz（默认 10000000；花屏就降到 4000000，稳了再往上加）",
    )
    ap.add_argument("--colstart", type=int, default=0, help="列偏移（默认 0）")
    ap.add_argument("--rowstart", type=int, default=0, help="行偏移（默认 0）")
    ap.add_argument(
        "--madctl", type=lambda s: int(s, 0), default=0x00,
        help="MADCTL 原始值（默认 0x00；0xC0=上下左右都翻转，0x60=旋转90°）",
    )
    ap.add_argument("--bgr", action="store_true", help="置 MADCTL 的 BGR 位（红蓝互换时用）")
    ap.add_argument("--no-invert", action="store_true", help="关闭反色（画面像负片时用）")
    ap.add_argument("--hold", action="store_true", help="跑完不退出，保持画面")
    args = ap.parse_args()

    madctl = args.madctl | (MADCTL_BGR if args.bgr else 0)

    banner("ST7789 点亮验证")
    print("  SPI 速度    : %.1f MHz" % (args.speed / 1e6))
    print("  colstart    : %d" % args.colstart)
    print("  rowstart    : %d" % args.rowstart)
    print("  MADCTL      : 0x%02X%s%s" % (madctl, " (+BGR)" if args.bgr else "",
                                           " (MX|MY=0xC0)" if madctl & (MADCTL_MX | MADCTL_MY) == (MADCTL_MX | MADCTL_MY) else ""))
    print("  反色        : %s" % ("关" if args.no_invert else "开"))

    lcd = ST7789(
        speed_hz=args.speed,
        width=240,
        height=240,
        colstart=args.colstart,
        rowstart=args.rowstart,
        madctl=madctl,
        invert=not args.no_invert,
    )

    try:
        banner("初始化")
        t0 = time.perf_counter()
        lcd.init()
        print("  初始化完成，耗时 %.0f ms" % ((time.perf_counter() - t0) * 1000))
        print("  （如果刚才屏幕闪了一下或者背光有变化，说明 RES/DC 接对了）")

        if args.mode in ("all", "solid"):
            test_solid(lcd)
        if args.mode in ("all", "bars"):
            test_bars(lcd)
            time.sleep(2.0)
        if args.mode in ("all", "gradient"):
            test_gradient(lcd)
            time.sleep(2.0)
        if args.mode in ("all", "text"):
            test_text(lcd, args.speed)
        if args.mode == "image":
            if not args.image:
                print("\n  ⚠️  --mode image 需要同时指定 --image <图片路径>")
            else:
                test_image(lcd, args.image, args.fit)

        banner("完成")
        print("  最后一个图案会留在屏上（脚本退出时不会清屏）")
        if args.hold:
            print("  --hold 已开启，按 Ctrl+C 退出")
            while True:
                time.sleep(1)
    except KeyboardInterrupt:
        print("\n  已中断")
    finally:
        lcd.close()
        print("  资源已释放")


if __name__ == "__main__":
    main()
