# ROCK 5B+ 官方文档链接清单
### 你的板子型号：ROCK 5B+ / `RS129-D16E0`（16GB LPDDR5，0GB eMMC）

---

## ⚠️ 用之前必读：5B 和 5B+ **共用同一套文档**

Radxa 把 ROCK 5B 和 ROCK 5B+ 放在**同一个文档目录** `/rock5/rock5b/` 下，页面上有**版本切换器**。

**危险之处**：很多页面**默认显示 ROCK 5B 的参数**，而 5B 和 5B+ 的关键区别是：

| | ROCK 5B | **你的 5B+** |
|---|---|---|
| WiFi | ❌ 无板载 WiFi | ✅ 板载 WiFi 6（RTL8852BE） |
| eMMC | 插槽（需另购模块） | 板载（你的 E0 = 0GB） |
| M.2 M-Key | 1 个 ×4 通道 | **2 个 ×2 通道** |
| 供电 | USB-C PD 30W | **USB-C PD 40W+** |
| 供电口 | 与 DP 复用 | **独立专用供电口** |
| HDMI IN | Micro HDMI | 全尺寸 HDMI |

**所以：看参数页时务必切到 "ROCK 5B+"。** URL 形式是加 `?versions=ROCK+5B%2B`，例如：

```
https://docs.radxa.com/en/rock5/rock5b/getting-started/introduction?versions=ROCK+5B%2B
```

---

## 0. 入口

| 用途 | 链接 |
|---|---|
| **产品页** | https://radxa.com/products/rock5/5bp/ |
| **文档首页（英文）** | https://docs.radxa.com/en/rock5/rock5b/ |
| **文档首页（中文）** ⭐ | https://docs.radxa.com/rock5/rock5b/ |
| **镜像仓库（GitHub）** | https://github.com/radxa-build/rock-5b-plus/releases |
| **官方下载站** | https://dl.radxa.com/ |
| **官方论坛（ROCK 5 板块）** | https://forum.radxa.com/c/rock5/5b |

> **中文文档就是把 `/en/` 去掉**，其余路径完全相同。下面我同时给两种。

---

## 1. 上手指南（你现在最需要的）

| 页面 | 英文 | 中文 |
|---|---|---|
| **产品介绍 / 参数表** ⭐ | [/en/rock5/rock5b/getting-started/introduction](https://docs.radxa.com/en/rock5/rock5b/getting-started/introduction?versions=ROCK+5B%2B) | [/rock5/rock5b/getting-started/introduction](https://docs.radxa.com/rock5/rock5b/getting-started/introduction?versions=ROCK+5B%2B) |
| **快速上手** | [/getting-started/quick-start](https://docs.radxa.com/en/rock5/rock5b/getting-started/quick-start) | [/getting-started/quick-start](https://docs.radxa.com/rock5/rock5b/getting-started/quick-start) |
| **安装系统** ⭐ | [/getting-started/install-os](https://docs.radxa.com/en/rock5/rock5b/getting-started/install-os) | [/getting-started/install-os](https://docs.radxa.com/rock5/rock5b/getting-started/install-os) |
| **首次软件配置** | [/getting-started/basic-software-conf](https://docs.radxa.com/en/rock5/rock5b/getting-started/basic-software-conf) | 同路径去 /en/ |
| **接口使用说明** | [/getting-started/interface-usage](https://docs.radxa.com/en/rock5/rock5b/getting-started/interface-usage) | 同路径去 /en/ |
| **电源选择** ⭐ | [/getting-started/power-supply](https://docs.radxa.com/en/rock5/rock5b/getting-started/power-supply) | 同路径去 /en/ |

**接口使用说明的子页面**（你都会用到）：

| 页面 | 链接 |
|---|---|
| FAN（风扇，2-pin 1.25mm） | [/getting-started/interface-usage/fan](https://docs.radxa.com/en/rock5/rock5b/getting-started/interface-usage/fan) |
| 耳机接口（3.5mm 耳麦二合一） | [/getting-started/interface-usage/headphone-jack](https://docs.radxa.com/en/rock5/rock5b/getting-started/interface-usage/headphone-jack) |
| USB 接口 | [/getting-started/interface-usage/usb](https://docs.radxa.com/en/rock5/rock5b/getting-started/interface-usage/usb) |
| eMMC 模块 | [/getting-started/interface-usage/emmc_module](https://docs.radxa.com/en/rock5/rock5b/getting-started/interface-usage/emmc_module) |
| PCIe M-Key（NVMe SSD） | [/getting-started/interface-usage/pcie-m-key](https://docs.radxa.com/en/rock5/rock5b/getting-started/interface-usage/pcie-m-key) |
| PCIe B-Key（仅 5B+，4G/5G） | [/getting-started/interface-usage/pcie-b-key](https://docs.radxa.com/en/rock5/rock5b/getting-started/interface-usage/pcie-b-key) |
| 40-PIN 功能测试 | [/getting-started/interface-usage/pin-40-test](https://docs.radxa.com/en/rock5/rock5b/getting-started/interface-usage/pin-40-test) |
| 资源下载汇总（镜像+工具） | [/rock5/rock5b/download](https://docs.radxa.com/en/rock5/rock5b/download) |

---

## 2. 系统（Radxa OS）

| 页面 | 链接 | 你会用它干什么 |
|---|---|---|
| **Radxa OS 总览** | [/radxa-os](https://docs.radxa.com/en/rock5/rock5b/radxa-os) | 全部分页索引 |
| **无屏模式（Headless）** ⭐⭐ | [/radxa-os/headless](https://docs.radxa.com/en/rock5/rock5b/radxa-os/headless) | **`before.txt` 预配 WiFi** 就在这里 |
| **UART 串口控制台** ⭐⭐ | [/radxa-os/serial](https://docs.radxa.com/en/rock5/rock5b/radxa-os/serial) | CH340 接 Pin 6/8/10、波特率 1500000 |
| **Rsetup 配置工具** ⭐ | [/radxa-os/rsetup](https://docs.radxa.com/en/rock5/rock5b/radxa-os/rsetup) | **开 Overlay（SPI/I2C/NPU）** |
| USB Net（USB 当网卡） | [/radxa-os/usbnet](https://docs.radxa.com/en/rock5/rock5b/radxa-os/usbnet) | 不接路由器的备用链路 |
| Wi-Fi 热点 | [/radxa-os/ap](https://docs.radxa.com/en/rock5/rock5b/radxa-os/ap) | 让板子自己发热点 |
| 启动参数 | [/radxa-os/bootparam](https://docs.radxa.com/en/rock5/rock5b/radxa-os/bootparam) | HDMI 不出图时强制分辨率 |
| 备份系统 | [/radxa-os/backup](https://docs.radxa.com/en/rock5/rock5b/radxa-os/backup) | 赛前做备用卡 |
| 自动登录 | [/radxa-os/autologin](https://docs.radxa.com/en/rock5/rock5b/radxa-os/autologin) | 演示时免登录 |

---

## 3. 硬件

| 页面 | 链接 |
|---|---|
| **硬件接口说明** ⭐（40Pin 引脚表、TF 座、PCIe、Maskrom 等） | [/hardware-design/hardware-interface](https://docs.radxa.com/en/rock5/rock5b/hardware-design/hardware-interface) |
| **从 40Pin 给板子供电** ⭐（25W 上限） | [/hardware-design/power_board_from_40pin](https://docs.radxa.com/en/rock5/rock5b/hardware-design/power_board_from_40pin) |
| **GPIO 引脚表（Wiki 版，更好查）** | https://wiki.radxa.com/Rock5/hardware/5b/gpio |

---

## 4. 应用开发

| 页面 | 链接 | 用途 |
|---|---|---|
| **Python 虚拟环境** ⭐ | [/app-development/venv-usage](https://docs.radxa.com/en/rock5/rock5b/app-development/venv-usage) | venv 用法（注意路径是 `venv-usage`，**连字符**） |
| **VS Code Remote SSH** ⭐ | [/app-development/vscode-remote-ssh](https://docs.radxa.com/en/rock5/rock5b/app-development/vscode-remote-ssh) | 你的主力开发方式 |
| **GPIOD 使用** | [/app-development/gpiod](https://docs.radxa.com/en/rock5/rock5b/app-development/gpiod) | 操作 GPIO（屏幕、按键） |
| **人工智能（29 篇）** ⭐⭐ | [/app-development/ai](https://docs.radxa.com/en/rock5/rock5b/app-development/ai) | **RKNN / RKLLM 全在这** |
| 应用开发总览 | [/app-development](https://docs.radxa.com/en/rock5/rock5b/app-development) | 含 OpenCV、Qt、RGA、ROS2 等 |

> **RKNN 安装的具体页面**（5B 的 AI 章节下，或参考已核实的 5T 版）：
> https://docs.radxa.com/en/rock5/rock5t/app-development/ai/rknn-install
> 里面有你需要的 **Python 版本兼容矩阵**。

---

## 5. 底层开发 / 救砖

| 页面 | 链接 |
|---|---|
| 底层开发总览 | [/low-level-dev](https://docs.radxa.com/en/rock5/rock5b/low-level-dev) |
| **MaskROM 模式刷写** | [/low-level-dev/install-os/rkdevtool_maskrom](https://docs.radxa.com/en/rock5/rock5b/low-level-dev/install-os/rkdevtool_maskrom) |
| SPI Flash 刷写 | [/low-level-dev/install-os/rkdevtool_spi](https://docs.radxa.com/en/rock5/rock5b/low-level-dev/install-os/rkdevtool_spi) |
| 擦除 SPI Flash | [/low-level-dev/install-os/erase_spi-flash](https://docs.radxa.com/en/rock5/rock5b/getting-started/install-os/erase_spi-flash) |

> ⚠️ **这些是 eMMC/SPI 的 USB 刷写工具链（RKDevTool），不是烧 SD 卡用的。** SD 卡就用 balenaEtcher。

---

## 6. 其他

| 页面 | 链接 |
|---|---|
| **FAQ** ⭐（起不来 / 无限重启 / HDMI 无输出） | [/rock5/rock5b/faq](https://docs.radxa.com/en/rock5/rock5b/faq) |
| 配件清单 | [/rock5/rock5b/accessories](https://docs.radxa.com/en/rock5/rock5b/accessories) |
| ROCK 5 系列通用 FAQ | [/rock5/faq](https://docs.radxa.com/en/rock5/faq) |

---

## 7. 你手上配件的文档

| 配件 | 链接 |
|---|---|
| **AE013B 散热器 = Heatsink 6240B** | https://docs.radxa.com/en/accessories/heatsink-case/heatsink-6240b |
| 官方 PD 电源 | https://docs.radxa.com/en/accessories/power |

---

## 8. 镜像下载（你要用的那一个）

**ROCK 5B+ 专用 Debian 镜像**（⚠️ 不是 5B 的）：

```
https://github.com/radxa-build/rock-5b-plus/releases/download/rsdk-r7/rock-5b-plus_bookworm_kde_r7.output_512.img.xz
```

- 文件名：`rock-5b-plus_bookworm_kde_r7.output_512.img.xz`
- 大小：**1.43 GB**
- 官方镜像仓库（查最新版）：https://github.com/radxa-build/rock-5b-plus/releases

> **注意**：Radxa 对 5B+ **只提供 Debian KDE 桌面版**，没有精简版/服务器版（官方说明：以前那些"不再提供，需要请自行构建"）。

---

## 9. 社区求助

| 渠道 | 链接 |
|---|---|
| 官方论坛 ROCK 5 板块 | https://forum.radxa.com/c/rock5/5b |
| 论坛总入口 | https://forum.radxa.com/ |
| GitHub 组织 | https://github.com/radxa |
| Discord | https://rock.sh/go |
| 文档纠错 / 提问 | https://github.com/radxa-docs/docs/issues |
