# -*- coding: utf-8 -*-
"""屏幕渲染 —— 给状态机用的通用绘制工具

把「想显示的内容」画成 ST7789 能直接推的 RGB565 缓冲。

────────────────────────────────────────────────────────
为什么不直接复用 tools/screen_bringup.py
────────────────────────────────────────────────────────
    screen_bringup 是「点亮验证」脚本，里面的 make_text 是**固定的测试图案**
    （四角定位块 + SPI 速率 + 假频谱）。
    这里需要的是**能显示任意文字、自动换行、自适应布局**的通用渲染。

────────────────────────────────────────────────────────
字体注意事项（踩过坑）
────────────────────────────────────────────────────────
    必须用 NotoSansCJK-Regular.ttc 的 **index=2 (SC)**。
    绝对不能用 DroidSansFallbackFull.ttf —— 它不含拉丁字母，英文会全变方块。

────────────────────────────────────────────────────────
用法
────────────────────────────────────────────────────────
    from kskbl.render import screens

    buf = screens.idle("待机中")
    lcd.show(buf)

    buf = screens.text_page("识别结果", "你刚才说的话……", footer="0.31s  20x")
    lcd.show(buf)
"""

import os

from PIL import Image, ImageDraw, ImageFont

from kskbl.st7789 import image_to_565_bytes

# 屏幕尺寸
WIDTH = 240
HEIGHT = 240

# 配色（深色背景，对比高，省得屏幕刺眼）
BG = (10, 12, 26)
BG_CARD = (22, 26, 46)
FG = (235, 238, 245)
FG_DIM = (140, 148, 170)
ACCENT = (90, 200, 255)        # 青
OK = (80, 230, 140)            # 绿
WARN = (255, 200, 80)          # 黄
HOT = (255, 110, 110)          # 红

# (路径, ttc 内索引)  —— index=2 是简体中文
FONT_CANDIDATES = [
    ("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc", 2),
    ("/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc", 2),
    ("/usr/share/fonts/truetype/wqy/wqy-microhei.ttc", 0),
    ("/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc", 0),
    ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 0),
]

_font_cache = {}
_font_path = None


def _pick_font():
    """挑一个中英文都全的字体，只做一次"""
    global _font_path
    if _font_path is not None:
        return _font_path
    for path, index in FONT_CANDIDATES:
        if not os.path.exists(path):
            continue
        try:
            ImageFont.truetype(path, 12, index=index)
            _font_path = (path, index)
            return _font_path
        except Exception:
            continue
    _font_path = (None, 0)
    return _font_path


def font(size):
    """按字号取字体（带缓存）"""
    if size in _font_cache:
        return _font_cache[size]
    path, index = _pick_font()
    if path is None:
        return ImageFont.load_default()
    f = ImageFont.truetype(path, size, index=index)
    _font_cache[size] = f
    return f


def new_canvas(bg=BG, width=WIDTH, height=HEIGHT):
    """新建画布，返回 (Image, ImageDraw)"""
    img = Image.new("RGB", (width, height), bg)
    return img, ImageDraw.Draw(img)


def wrap_text(draw, text, f, max_width):
    """按像素宽度自动换行（中英文都适用）

    中文按字符断行，英文尽量按单词断行。
    """
    if not text:
        return []

    lines = []
    for para in text.split("\n"):
        if not para:
            lines.append("")
            continue
        cur = ""
        for ch in para:
            trial = cur + ch
            if draw.textlength(trial, font=f) <= max_width or not cur:
                cur = trial
            else:
                # 英文单词尽量整体换行
                if ch.isascii() and ch not in " ,.!?;:" and " " in cur:
                    head, _, tail = cur.rpartition(" ")
                    if head:
                        lines.append(head)
                        cur = tail + ch
                        continue
                lines.append(cur)
                cur = ch
        if cur:
            lines.append(cur)
    return lines


def _center(draw, text, y, f, fill, width=WIDTH):
    w = draw.textlength(text, font=f)
    draw.text(((width - w) / 2, y), text, font=f, fill=fill)


def _draw_text_block(draw, text, box, f, fill, line_spacing=3, max_lines=None):
    """在 box=(x0, y0, x1, y1) 里画自动换行的文字块，返回实际画了几行"""
    x0, y0, x1, y1 = box
    lines = wrap_text(draw, text, f, x1 - x0)
    line_h = f.size + line_spacing

    if max_lines is not None and len(lines) > max_lines:
        lines = lines[:max_lines]
        if lines:
            # 最后一行加省略号
            last = lines[-1]
            while last and draw.textlength(last + "…", font=f) > (x1 - x0):
                last = last[:-1]
            lines[-1] = last + "…"

    y = y0
    drawn = 0
    for line in lines:
        if y + f.size > y1:
            break
        draw.text((x0, y), line, font=f, fill=fill)
        y += line_h
        drawn += 1
    return drawn


def to_buffer(img):
    """PIL Image -> RGB565 大端 bytes（可以直接丢给 lcd.show）"""
    return image_to_565_bytes(img)


# ══════════════════════════════════════════════════════════════
# 各种屏幕
# ══════════════════════════════════════════════════════════════
class Screens(object):
    """常用的几种屏幕画面，全部返回 RGB565 缓冲"""

    def __init__(self, width=WIDTH, height=HEIGHT):
        self.width = width
        self.height = height

    # ── 待机 ─────────────────────────────────────────────
    def idle(self, subtitle="待机中", footer="AI 语音链路就绪"):
        """开屏 / 待机画面"""
        img, d = new_canvas(width=self.width, height=self.height)

        f_title = font(26)
        f_mid = font(17)
        f_sml = font(13)

        _center(d, "会听会变声的", 46, f_title, FG)
        _center(d, "智能终端", 78, f_title, ACCENT)

        # 中间分隔线
        d.line([(40, 130), (self.width - 40, 130)], fill=(50, 56, 84), width=1)

        _center(d, "🎤 " + subtitle, 148, f_mid, OK)

        if footer:
            _center(d, footer, self.height - 26, f_sml, FG_DIM)

        return to_buffer(img)

    # ── 录音中（带音量条）────────────────────────────────
    def listening(self, levels=None, hint="请说话…"):
        """录音画面。levels 是 0~1 的列表，用来画音量条"""
        img, d = new_canvas(width=self.width, height=self.height)

        f_title = font(20)
        f_sml = font(13)

        d.ellipse([(20, 24), (44, 48)], fill=HOT)
        d.text((54, 28), "正在录音", font=f_title, fill=FG)

        # 音量条
        n = 16
        bar_w = 10
        gap = 4
        total = n * bar_w + (n - 1) * gap
        x0 = (self.width - total) // 2
        base_y = 150
        max_h = 70

        levels = levels or [0.0] * n
        # 只取最后 n 个
        levels = list(levels)[-n:]
        levels = [0.0] * (n - len(levels)) + levels

        for i in range(n):
            v = max(0.0, min(1.0, float(levels[i])))
            h = max(2, int(max_h * v))
            x = x0 + i * (bar_w + gap)
            # 低=绿，中=黄，高=红
            if v < 0.6:
                color = OK
            elif v < 0.85:
                color = WARN
            else:
                color = HOT
            if h <= 2:
                color = (40, 46, 70)
            d.rectangle([(x, base_y - h), (x + bar_w, base_y)], fill=color)

        _center(d, hint, base_y + 16, f_sml, FG_DIM)
        return to_buffer(img)

    # ── 处理中 ───────────────────────────────────────────
    def busy(self, title="识别中…", detail=""):
        img, d = new_canvas(width=self.width, height=self.height)
        f_title = font(22)
        f_sml = font(14)

        _center(d, "⏳", 70, font(40), WARN)
        _center(d, title, 130, f_title, FG)
        if detail:
            _center(d, detail, 168, f_sml, FG_DIM)
        return to_buffer(img)

    # ── 通用文字页 ───────────────────────────────────────
    def text_page(self, title, body, footer="", title_color=ACCENT,
                  body_size=16):
        """标题 + 正文（自动换行）+ 页脚"""
        img, d = new_canvas(width=self.width, height=self.height)
        f_title = font(17)
        f_body = font(body_size)
        f_sml = font(12)

        draw_header = 20
        if title:
            # 标题左边加一条色条
            d.rectangle([(14, draw_header + 2), (18, draw_header + 20)],
                        fill=title_color)
            d.text((26, draw_header), title, font=f_title, fill=FG)
            top = draw_header + 34
        else:
            top = draw_header

        # 正文底板
        d.rounded_rectangle([(12, top - 6), (self.width - 12, self.height - 42)],
                            radius=8, fill=BG_CARD)

        max_lines = max(1, (self.height - 42 - (top - 6) - 12) // (body_size + 3))
        _draw_text_block(d, body,
                         (22, top + 2, self.width - 22, self.height - 48),
                         f_body, FG, max_lines=max_lines)

        if footer:
            _center(d, footer, self.height - 28, f_sml, FG_DIM)

        return to_buffer(img)

    def plain(self, text, size=20, center=True):
        """**只显示一段文字** —— 没有标题、页脚、卡片或装饰。

        演示时用这个把识别到的话直接打在屏幕上，干净利落。
        文字过长会自动换行，行数过多时垂直居中并截断。
        """
        img, d = new_canvas(width=self.width, height=self.height)
        f = font(size)
        pad = 16
        x0, x1 = pad, self.width - pad
        y0, y1 = pad, self.height - pad

        if center:
            lines = wrap_text(d, text, f, x1 - x0)
            total_h = len(lines) * (f.size + 3)
            y0 = pad + max(0, (self.height - 2 * pad - total_h) // 2)

        _draw_text_block(d, text, (x0, y0, x1, y1), f, FG)
        return to_buffer(img)

    def progress(self, title="合成中", ratio=0.0, unit=""):
        """标题 + 进度条 + 百分比 —— 耗时较长的处理用这个给用户反馈。

        ratio 是 0.0~1.0。调用方自己决定怎么推进，
        比如按时间做渐进式进度（越接近完成越发慢）。
        """
        img, d = new_canvas(width=self.width, height=self.height)
        ratio = max(0.0, min(1.0, float(ratio)))

        _center(d, title, 86, font(18), FG)

        x0, x1 = 28, self.width - 28
        y, h = 124, 12
        d.rounded_rectangle([(x0, y), (x1, y + h)], radius=6,
                            fill=BG_CARD, outline=FG_DIM)
        w = int((x1 - x0 - 4) * ratio)
        if w > 3:
            d.rounded_rectangle([(x0 + 2, y + 2), (x0 + 2 + w, y + h - 2)],
                                radius=5, fill=ACCENT)

        pct = "%d%%" % int(round(ratio * 100))
        _center(d, pct + ("  " + unit if unit else ""), 154, font(13), FG_DIM)
        return to_buffer(img)

    def speech_progress(self, text, ratio=0.0, title="合成中…"):
        """上半屏显示识别到的文字，下半屏显示进度条。

        音色克隆要十几秒，把"要念的内容"和"正在处理"同时摆在屏幕上，
        用户一眼就知道系统在做什么，而不是干等一个光秃秃的进度条。

        布局（240×240）：
            0   ~ 112   识别文字（最多 4 行）
            118        分隔线
            126        标题（合成中…）
            152 ~ 164  进度条
            176        百分比
        """
        img, d = new_canvas(width=self.width, height=self.height)
        ratio = max(0.0, min(1.0, float(ratio)))
        pad = 14

        # ── 上半屏：识别到的文字 ──
        _draw_text_block(d, text, (pad, 10, self.width - pad, 112),
                         font(17), FG, max_lines=4)

        # ── 分隔线 ──
        d.line([(pad, 118), (self.width - pad, 118)], fill=FG_DIM)

        # ── 下半屏：进度条 ──
        _center(d, title, 124, font(14), FG_DIM)

        x0, x1 = 26, self.width - 26
        y, h = 152, 12
        d.rounded_rectangle([(x0, y), (x1, y + h)], radius=6,
                            fill=BG_CARD, outline=FG_DIM)
        w = int((x1 - x0 - 4) * ratio)
        if w > 3:
            d.rounded_rectangle([(x0 + 2, y + 2), (x0 + 2 + w, y + h - 2)],
                                radius=5, fill=ACCENT)

        _center(d, "%d%%" % int(round(ratio * 100)), 174, font(13), FG_DIM)
        return to_buffer(img)

    # ── 错误 ─────────────────────────────────────────────
    def error(self, message, detail=""):
        img, d = new_canvas(width=self.width, height=self.height)
        f_title = font(20)
        f_sml = font(13)

        _center(d, "⚠", 62, font(44), HOT)
        _center(d, message, 128, f_title, HOT)
        if detail:
            _draw_text_block(d, detail, (24, 164, self.width - 24, 200),
                             f_sml, FG_DIM)
        return to_buffer(img)


# 模块级的默认实例，方便直接 import 使用
screens = Screens()
