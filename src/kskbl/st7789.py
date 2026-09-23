# -*- coding: utf-8 -*-
"""ST7789 (240x240, 4 线 SPI) 最小驱动 —— ROCK 5B+ 40-pin 直连

本文件是「会听会变声的智能终端」显示子系统的底层驱动。
只做一件事：把一整帧 RGB565 数据推到屏幕上。

────────────────────────────────────────────────────────
接线（已在实机上用 gpiofind 验证过）
────────────────────────────────────────────────────────
    屏幕丝印        接到 ROCK 5B+ 40-pin
    GND       ->    Pin 6   (GND)
    VCC       ->    Pin 1   (3.3V)  ⚠️ 严禁 5V
    SCL/CLK   ->    Pin 23  (SPI0_SCLK)
    SDA/DIN   ->    Pin 19  (SPI0_MOSI)
    CS        ->    Pin 24  (SPI0_CE0)
    DC        ->    Pin 18  -> gpiochip4 line 20
    RES/RST   ->    Pin 16  -> gpiochip3 line 4
    BLO/BL    ->    Pin 17  (3.3V, 背光常亮)

────────────────────────────────────────────────────────
依赖
────────────────────────────────────────────────────────
    sudo apt install -y python3-spidev python3-libgpiod python3-numpy python3-pil

    ⚠️ 这四个必须装成「系统包」，venv 要用 --system-site-packages 才看得到它们。
       尤其 gpiod 不能 pip 装（PyPI 版要 libgpiod>=2.0，Debian 12 只有 1.6.3）。

────────────────────────────────────────────────────────
关于性能（2026-09-23 在 ROCK 5B+ 上实测）
────────────────────────────────────────────────────────
    一帧 = 240 × 240 × 2 字节 = 115,200 字节

    请求速率    实测帧率    画面
    10 MHz      10.7 fps    ✅
    16 MHz      14.9 fps    ✅
    20 MHz      21.2 fps    ✅
    24 MHz      20.6 fps    ⚠️ 分频后反而比 20 MHz 更慢，禁用
    30 MHz      25.5 fps    ✅  ← 本项目采用
    40 MHz      33.5 fps    ❌ 直接黑屏

    ⚠️ 24 MHz 会被 RK3588 的 SPI 分频器向下取整到比 20 MHz 更低的一档，
       实测更慢，**千万不要用**。
    ⚠️ 40 MHz 超过 ST7789 的时序上限，初始化命令本身就收不到 → 全黑。
       这是非破坏性的：重新以 ≤30 MHz 初始化立刻恢复，不会烧屏。
    → 本类默认 10 MHz 只是「首次点亮求稳」用；正式跑频谱请传 speed_hz=30_000_000。
"""

import struct
import time

try:
    import spidev
except ImportError as exc:  # pragma: no cover
    raise ImportError("缺少 spidev，请执行：sudo apt install python3-spidev") from exc

try:
    import gpiod
except ImportError as exc:  # pragma: no cover
    raise ImportError("缺少 gpiod，请执行：sudo apt install python3-libgpiod") from exc


# ── ST7789 命令字 ────────────────────────────────────────────────
SWRESET = 0x01
SLPOUT = 0x11
NORON = 0x13
INVOFF = 0x20
INVON = 0x21
DISPOFF = 0x28
DISPON = 0x29
CASET = 0x2A
RASET = 0x2B
RAMWR = 0x2C
MADCTL = 0x36
COLMOD = 0x3A

# MADCTL 位定义
MADCTL_MY = 0x80   # 行地址方向
MADCTL_MX = 0x40   # 列地址方向
MADCTL_MV = 0x20   # 行列交换
MADCTL_ML = 0x10
MADCTL_BGR = 0x08  # 1 = BGR，0 = RGB
MADCTL_MH = 0x04


def rgb565(r, g, b):
    """(R, G, B) 8bit -> RGB565 16bit 整数"""
    return ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)


def solid_buffer(width, height, color):
    """生成整屏纯色缓冲。

    color 可以是 (r, g, b) 元组，也可以是已经算好的 RGB565 整数。
    返回 big-endian 的 bytes，长度 = width * height * 2。
    """
    value = color if isinstance(color, int) else rgb565(*color)
    return struct.pack(">H", value) * (width * height)


def rgb_array_to_565_bytes(arr):
    """(H, W, 3) 的 uint8/uint16 RGB 数组 -> RGB565 big-endian bytes"""
    import numpy as np

    a = np.asarray(arr, dtype=np.uint16)
    if a.ndim != 3 or a.shape[2] != 3:
        raise ValueError("需要 (H, W, 3) 的 RGB 数组，实际是 %r" % (a.shape,))
    value = ((a[..., 0] & 0xF8) << 8) | ((a[..., 1] & 0xFC) << 3) | (a[..., 2] >> 3)
    return value.astype(">u2").tobytes()


def image_to_565_bytes(img):
    """PIL Image -> RGB565 big-endian bytes（自动转 RGB）"""
    import numpy as np

    return rgb_array_to_565_bytes(np.asarray(img.convert("RGB")))


class ST7789(object):
    """ST7789 240x240 屏，走 /dev/spidevX.Y + 两条 GPIO 控制线。"""

    def __init__(
        self,
        speed_hz=10_000_000,
        spi_bus=0,
        spi_dev=0,
        width=240,
        height=240,
        colstart=0,
        rowstart=0,
        madctl=0x00,
        invert=True,
        dc_pin=("gpiochip4", 20),
        rst_pin=("gpiochip3", 4),
    ):
        self.width = width
        self.height = height
        self.colstart = colstart
        self.rowstart = rowstart
        self.madctl_value = madctl
        self.invert = invert
        self.speed_hz = speed_hz

        # ── SPI ──
        self.spi = spidev.SpiDev()
        self.spi.open(spi_bus, spi_dev)
        self.spi.max_speed_hz = speed_hz
        self.spi.mode = 0
        self.spi.cshigh = False
        self.spi.lsbfirst = False
        self.spi.bits_per_word = 8

        # ── GPIO（DC 和 RES 不在同一个 gpiochip 上，所以要开两个）──
        self._chips = []
        self._lines = []
        self.dc = self._claim(dc_pin, "st7789-dc")
        self.rst = self._claim(rst_pin, "st7789-rst")

    # ── 内部工具 ─────────────────────────────────────────────
    def _claim(self, pin, consumer):
        chip_name, line_no = pin
        chip = gpiod.Chip(chip_name)
        line = chip.get_line(line_no)
        line.request(
            consumer=consumer, type=gpiod.LINE_REQ_DIR_OUT, default_vals=[0]
        )
        self._chips.append(chip)
        self._lines.append(line)
        return line

    def _cmd(self, cmd, data=None):
        """发命令（DC=0），可选带参数（参数部分自动切 DC=1）"""
        self.dc.set_value(0)
        self.spi.writebytes([cmd])
        if data:
            self.dc.set_value(1)
            self.spi.writebytes2(bytes(data))

    # ── 初始化 ───────────────────────────────────────────────
    def reset(self):
        """硬件复位：RES 拉低 20ms 再拉高"""
        self.rst.set_value(1)
        time.sleep(0.010)
        self.rst.set_value(0)
        time.sleep(0.020)
        self.rst.set_value(1)
        time.sleep(0.150)

    def init(self):
        """标准 240x240 初始化序列"""
        self.reset()

        self._cmd(SWRESET)
        time.sleep(0.150)
        self._cmd(SLPOUT)
        time.sleep(0.120)

        self._cmd(COLMOD, b"\x55")                          # 16bit/pixel = RGB565
        self._cmd(MADCTL, bytes([self.madctl_value]))       # 扫描方向 / RGB-BGR

        self._cmd(0xB2, b"\x0C\x0C\x00\x33\x33")            # Porch setting
        self._cmd(0xB7, b"\x35")                            # Gate control
        self._cmd(0xBB, b"\x19")                            # VCOM
        self._cmd(0xC0, b"\x2C")                            # LCM control
        self._cmd(0xC2, b"\x01")                            # VDV/VRH enable
        self._cmd(0xC3, b"\x12")                            # VRH
        self._cmd(0xC4, b"\x20")                            # VDV
        self._cmd(0xC6, b"\x0F")                            # 帧率
        self._cmd(0xD0, b"\xA4\xA1")                        # Power control
        self._cmd(0xE0, b"\xD0\x04\x0D\x11\x13\x2B\x3F\x54"
                        b"\x4C\x18\x0D\x0B\x1F\x23")        # 正极性伽马
        self._cmd(0xE1, b"\xD0\x04\x0C\x11\x13\x2C\x3F\x44"
                        b"\x51\x2F\x1F\x1F\x20\x23")        # 负极性伽马

        self._cmd(INVON if self.invert else INVOFF)         # IPS 屏一般要反色
        self._cmd(NORON)                                    # 正常工作模式
        self._cmd(DISPON)
        time.sleep(0.100)

    # ── 显示 ─────────────────────────────────────────────────
    def set_window(self, x0, y0, x1, y1):
        """设置写入区域（含 colstart / rowstart 偏移）"""
        self._cmd(CASET, struct.pack(">HH", x0 + self.colstart, x1 + self.colstart))
        self._cmd(RASET, struct.pack(">HH", y0 + self.rowstart, y1 + self.rowstart))
        self._cmd(RAMWR)

    def show(self, buf):
        """推一整帧。buf 必须是 width*height*2 字节的 RGB565 大端数据。"""
        expected = self.width * self.height * 2
        if len(buf) != expected:
            raise ValueError(
                "缓冲长度不对：期望 %d 字节，实际 %d 字节" % (expected, len(buf))
            )
        self.set_window(0, 0, self.width - 1, self.height - 1)
        self.dc.set_value(1)
        self.spi.writebytes2(buf)

    def fill(self, color):
        """整屏填充一个颜色，color 可以是 (r,g,b) 或 RGB565 整数"""
        self.show(solid_buffer(self.width, self.height, color))

    def display_off(self):
        self._cmd(DISPOFF)

    def display_on(self):
        self._cmd(DISPON)

    # ── 收尾 ─────────────────────────────────────────────────
    def close(self):
        """释放资源。

        注意：故意不发 DISPOFF —— 这样进程退出后画面还留在屏上，
        方便肉眼确认最后一个测试图案。同时把 RES 拉高（非复位态）。
        """
        try:
            self.rst.set_value(1)
            self.dc.set_value(0)
        except Exception:
            pass
        for line in self._lines:
            try:
                line.release()
            except Exception:
                pass
        for chip in self._chips:
            try:
                chip.close()
            except Exception:
                pass
        try:
            self.spi.close()
        except Exception:
            pass

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        self.close()
