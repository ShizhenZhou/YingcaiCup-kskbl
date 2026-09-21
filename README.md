# YingcaiCup-kskbl

**会听会变声的灯** —— 在 ROCK 5B+（RK3588）上做实时变声，并让灯随声音律动。

- 麦克风采集 → 实时变声处理 → 耳机/扬声器输出，**全程本地运行，不依赖云服务**
- 音量 / 节拍驱动 LED 灯效（ESP32）
- 目标平台：ROCK 5B+ / 16GB，Radxa OS（Debian 12 / kernel 6.1.84-8-rk2410）

## 当前状态

| 环节 | 状态 |
|---|---|
| 板卡系统配置（SSH / 时区 / APT 换源 / 系统升级） | 完成 |
| 音频输出（板载 ES8316 → 3.5mm） | 已验证，重启自动生效 |
| 音频采集链路（ADC → I2S → ALSA） | 链路已验证 |
| 麦克风硬件 | 待 USB 声卡到货 |
| 变声算法（DSP） | 未开始 |
| 灯光律动（ESP32） | 未开始 |

## 目录结构

    .
    ├── docs/         项目文档（方案、采购、进度、实机实测记录）
    ├── src/kskbl/    Python 程序主体
    ├── experiments/  探索性脚本（一次性试验，非交付物）
    └── tools/        运维 / 校验脚本

（tests/ 与 firmware/ 在写第一个测试、ESP32 到货后再建。）

## 快速开始

### 板子侧（运行环境）

系统依赖：

    sudo apt install -y libportaudio2

Python 环境：

    python3 -m venv ~/proj/venv
    source ~/proj/venv/bin/activate
    pip install -r requirements.txt

### 开发侧（Windows）

代码在本仓库编辑，通过 scp / rsync 部署到板子运行。
**板子上不保存任何 Git 凭据。**

## 文档导航

| 文档 | 内容 |
|---|---|
| [落地方案](docs/零基础校赛_落地方案.md) | 作品定位、三级交付目标、核心 BOM |
| [八周进度表](docs/八周进度表_9月到11月.md) | 逐周计划与三个硬门槛 |
| [采购清单](docs/ROCK5Bplus_电赛采购清单.md) | 完整 BOM 与规格论证 |
| [音频子系统笔记](docs/ROCK5Bplus_音频子系统笔记.md) | ES8316 踩坑全记录，**改音频代码前必读** |
| [系统配置与镜像源实测](docs/ROCK5Bplus_系统配置与镜像源实测.md) | APT 换源实测、系统升级记录 |
| [无显示器方案](docs/无显示器方案_串口与远程.md) | 串口 / SSH / 远程桌面 |
| [开发语言与工作流](docs/开发语言与工作流.md) | 语言分工、VS Code Remote-SSH |
| [开发环境配置](docs/开发环境配置.md) | 各环境说明与 FAQ |
| [存储需求评估](docs/存储需求评估.md) | 容量测算与 SD 卡选型 |
| [官方文档链接](docs/ROCK5Bplus_官方文档链接.md) | Radxa 官方文档索引 |
| [文档索引](docs/README.md) | 项目全景与当前状态 |

## 三条音频铁律

1. 声卡永远用**卡名** `rockchipes8316`，不要用 `-c 4` 这类编号 —— 重启后会漂移
2. `amixer sget` **看不到**真正的控件名，全名控件必须用 `cget` / `cset`
3. 程序直接使用硬件设备 `plughw:CARD=rockchipes8316,DEV=0`，绕开 PipeWire

详见 [音频子系统笔记](docs/ROCK5Bplus_音频子系统笔记.md)。