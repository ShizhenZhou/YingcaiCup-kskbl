# ROCK 5B+ 系统配置与 APT 镜像源实测

> 记录日期：2026-09-21
> 板卡：ROCK 5B+（RS129-D16E0）｜系统：Radxa OS / Debian 12 bookworm / kernel `6.1.84-8-rk2410`
> 网络环境：**电子科技大学（四川成都）校园网**，CERNET 教育网出口

---

## 1. 板卡与网络基线

| 项目 | 值 |
|---|---|
| 主机名 | `rock-5b-plus` |
| 地址 | `192.168.1.100`（mDNS `rock-5b-plus.local`） |
| 无线接口 | `wlP2p33s0`，MAC `XX:XX:XX:XX:XX:XX` |
| 上级路由 | 一台 WiFi 6 路由器 |
| 频段/信道 | 5GHz，channel 40（5200 MHz），80 MHz 带宽 |
| 协商速率 | **rx/tx 1200.9 MBit/s**（802.11ax HE-MCS 11 HE-NSS 2） |
| 信号 | **−31 dBm**（极佳），发射功率 20.00 dBm（上限） |
| 出口性质 | **CERNET 教育网**（USTC 解析到 CERNET2 IPv6 `2001:da8:...`） |
| IPv6 | 有全局地址 + 默认路由 |

**无线链路结论：RTL8852BE 工作完全正常，已达 2×2 80MHz WiFi 6 满速。**
但音频采集与播放都在板子本地完成、不经网络，**无线速率不会改善音频延迟**。

---

## 2. APT 镜像源实测（核心结论）

### 2.1 换源前的原始状态

```
/etc/apt/sources.list.d/50-bookworm*.list    → deb.debian.org
/etc/apt/sources.list.d/70-radxa.list        → radxa-repo.github.io/bookworm
/etc/apt/sources.list.d/80-radxa-rk3588.list → radxa-repo.github.io/rk3588-bookworm
```

`radxa-repo.github.io` 是 GitHub Pages，国内拉取极慢。

### 2.2 实测速率（板子上 curl 实测，单位 MB/s）

| 镜像站 | Debian 主索引实测 | 结论 |
|---|---|---|
| **`deb.debian.org`（原始）** | 1.37 MB / **60s 超时** ≈ **23 KB/s** | ❌ 灾难级 |
| **兰州大学 `mirrors.lzu.edu.cn`** | 8.69 MB / 1.18s、1.54s、1.74s、1.88s ≈ **4–7 MB/s** | ⭐ **最优，5 次全部成功** |
| 南京大学 `mirror.nju.edu.cn` | 8.69 MB / 2.8s、3.1s ≈ 2.7 MB/s | 🥈 备选 |
| 中科大 `mirrors.ustc.edu.cn` | 8.0 MB / 3.8s ≈ 2.0 MB/s | 一般 |
| 华中科技大学 `mirrors.hust.edu.cn` | 一次 8 MB、一次仅 170 KB | ⚠️ 不稳定 |
| 清华 TUNA | 固定只返回 **15,345 B** | ⚠️ **疑似被网络拦截，勿用** |
| 重庆大学 `mirrors.cqu.edu.cn` | DNS 可解析（`202.202.1.140`），**IPv4 25s 超时、IPv6 无响应** | ❌ 连不上 |
| 重庆邮电大学 `mirrors.cqupt.edu.cn` | 8.69 MB / 15.9s ≈ **0.55 MB/s**；**不托管 Radxa 源（404）** | ❌ 慢 10 倍 |

### 2.3 兰州大学细节

```
IPv4  202.201.2.130       1.32s / 1.41s
IPv6  2001:da8:c000:a::2  1.60s / 1.67s
mirror.lzu.edu.cn（单数）与 mirrors.lzu.edu.cn（复数）是同一台机器
```

### 2.4 「一个站覆盖两个源」

rsetup 的可选列表**交集只有兰州大学**同时满足：

```
Radxa  列表: aghost / lzu / hust / sdu / nju / nyist
Debian 列表: ustc / tuna / lzu / hust / sdu / nju / nyist
                      ↑ 交集，且实测最快
```

已验证 LZU 上的 Radxa 仓库真实可用：

```
Suite: rk3588-bookworm      Architectures: all arm64 armhf riscv64
Components: main            Release: 13,145 B（与 hust/nju 完全一致）
```

> 附注：Radxa 的 rk3588 源**只提供 `Packages.gz`，没有 `.xz`**。
> 访问 `Packages.xz` 会返回 `404` + 146 字节页面——**这是正常的**，apt 会自己挑 `.gz`。
> 我一度把 146 B 误判为"限速"，实为 404 页面大小。

### 2.5 换源结果

经 `sudo rsetup → System → Change APT Sources` 两处均选兰州大学，最终源文件：

```
deb https://mirrors.lzu.edu.cn/debian bookworm main contrib non-free non-free-firmware
deb https://mirrors.lzu.edu.cn/debian bookworm-backports ...
deb https://mirrors.lzu.edu.cn/debian bookworm-updates ...
deb https://mirrors.lzu.edu.cn/debian-security bookworm-security ...
deb [signed-by=...] https://mirrors.lzu.edu.cn/radxa-deb/bookworm bookworm main
deb [signed-by=...] https://mirrors.lzu.edu.cn/radxa-deb/rk3588-bookworm rk3588-bookworm main
```

实测效果：

```
Fetched 32.2 MB in 6s (5,155 kB/s)
```

**换源把速度提升了约 220 倍**（23 KB/s → 5.1 MB/s），且无任何 `Err:` 或 GPG 错误。

---

## 3. 电子科技大学没有开源镜像站（已穷尽排查）

三个候选域名**全部 NXDOMAIN**，且用 7 个 DNS 服务器交叉验证：

```
mirrors.uestc.cn         系统默认 / 223.5.5.5 / 119.29.29.29 / 114.114.114.114
mirrors.uestc.edu.cn     / 8.8.8.8 / 1.1.1.1 / 202.112.14.151(CERNET)
mirror.uestc.edu.cn      → 全部 "DNS name does not exist"
```

**关键对照实验**（排除"网络不通"的可能）：

```
curl http://222.197.166.2/     →  HTTP 502（0.26s，收到 2734 字节）
```

板子**能连上电子科大的服务器**，说明链路、防火墙、校园网认证都正常——**纯粹是 `mirrors` 这台主机没有被发布**。

**子域名穷举**：`mirrors / mirror / debian / ubuntu / ftp / oss / repo / apt / software / download / linux / source / open / yum / pip / pypi / conda / docker / cdn / files / samba / proxy / cache` × `uestc.edu.cn` / `uestc.cn` = **44 次查询全部为空**。

同期对照组全部正常解析：`ustc` / `nju` / `lzu` / `cqu` / `cqupt` / `sdu` / `nyist`。

**第三方印证**：[LinuxMirrors 中国大陆教育网镜像站列表](https://linuxmirrors.cn/mirrors/) 列出 23 所高校，**电子科技大学不在其中**。同城的四川大学 `mirrors.scu.edu.cn`、西南交大 `mirrors.swjtu.edu.cn` 也不存在。

> 可能的误解来源：**电子科大是 CERNET 西南地区主节点**，容易让人以为"地区中心必有镜像站"。
> 地区网络中心与镜像站是两回事，运营镜像站是各校自发行为。

---

## 4. PyPI 源：保持阿里云，不要改

针对校园网实测（1.3 MB 索引）：

| 源 | 速度 |
|---|---|
| **`mirrors.aliyun.com`（现用）** | **4.70 MB/s** ⭐ |
| 中科大 `mirrors.ustc.edu.cn/pypi` | 1.23 MB/s |
| 南京大学 `mirror.nju.edu.cn/pypi/web` | 0.60 MB/s |
| 清华 `pypi.tuna.tsinghua.edu.cn` | 仅 ~15 KB（被拦截） |

结论：**公网的阿里云在这张校园网上反而比教育网 pypi 更快。**

当前配置（`~/.config/pip/pip.conf`）：

```ini
[global]
index-url = https://mirrors.aliyun.com/pypi/simple/
```

---

## 5. 系统升级记录

`apt update` 后有 28 个可升级包，**全部是 Debian 官方安全补丁**（`oldstable-security`）：

- ❌ 无 `linux-image-*` → **内核 `6.1.84-8-rk2410` 未被改动**（升级后已核实）
- ❌ 无 `u-boot` / 引导程序 → 启动链未动
- ❌ 无任何 `radxa-*` 包
- ❌ 无 WiFi/GPU 固件
- ✅ 只有 `libexpat1`、`liblzma5`、`xz-utils`、`libarchive13`、`ca-certificates`、`poppler`、`libnss3`、`firefox-esr`、`xserver-xorg-core` 等用户态库

**结论：不存在"升级后起不来"的风险路径**，可放心升级。

### 升级过程中的 needrestart 交互

弹窗「Daemons using outdated libraries」——**包此时已安装完成**，它只问要不要重启仍在用旧库的守护进程。

⚠️ **关键操作**：必须**取消勾选 `ssh.service`**！因为板子无显示器、唯一入口就是从笔记本 SSH 进去，而 systemd 下 `systemctl restart ssh` 会终止该服务整个 cgroup（**包括当前会话**）。
同时确认 `NetworkManager.service`（管 WiFi）保持未勾选。

实际被推迟重启的（正是危险清单）：

```
Service restarts being deferred:
 systemctl restart NetworkManager.service
 systemctl restart bluetooth.service
 systemctl restart dbus.service
 systemctl restart gdm.service / gdm3.service
 systemctl restart ssh.service            ← 会话保住了
 systemctl restart systemd-logind.service
 systemctl restart wpa_supplicant.service
```

升级结果：`0 upgraded, 0 newly installed, 0 to remove and 0 not upgraded.`

---

## 6. 重启后健康检查（升级验证）

| 项目 | 结果 |
|---|---|
| 内核 | `6.1.84-8-rk2410` — 未改动 ✅ |
| 启动耗时 | kernel 3.137s + userspace 9.702s = **12.840s** |
| 失败单元 | **0 个** ✅ |
| WiFi | `wlP2p33s0` 已连接，−31 dBm ✅ |
| 温度 | 7 个热区全部 **35–36 °C**，风扇 PWM=0（闲时停转，正常） |
| 内存 | 15Gi 总量 / 554Mi 已用 |
| 磁盘 | `/dev/mmcblk1p3` 59G，用 5.9G，剩 50G（11%） |
| 声卡 | `rockchipes8316` 在位 ✅ |

---

## 7. 已完成的基础配置清单

| # | 项目 | 状态 |
|---|---|---|
| ① | SSH 免密登录（密钥 `%USERPROFILE%\.ssh\rock5b`） | ✅ |
| ② | 串口权限 `dialout` | ✅ |
| ③ | `passwd` 修改登录密码 | ⏭️ 暂不处理 |
| ④ | 时区 `Asia/Shanghai` | ✅（原为 `Etc/UTC`） |
| ⑤ | APT 换源兰州大学 | ✅ |
| ⑥ | 系统升级 + 重启验证 | ✅ |
| ⑦ | 关闭 KDE 图形界面 | ⏸️ **决定跳过**（内存仅用 554Mi、启动 12.8s，收益太小） |
| ⑧ | `iw` 可用 | ✅（见下方陷阱） |

### `iw` 的 PATH 陷阱

`iw` 一直在 `/sbin/iw`（`/usr/sbin/iw` 同一份），**但 `/usr/sbin` 和 `/sbin` 不在普通用户的 PATH 里**——这是 Debian 的设计。

**「命令找不到」≠「没装」。** 三条排查命令：

```bash
dpkg -l iw                  # 行首是否 ii
dpkg -L iw | grep bin       # 文件在哪
command -v iw               # 当前 PATH 找得到吗
```

已加入 `~/.bashrc`：`export PATH="$PATH:/usr/sbin:/sbin"`

> ⚠️ **重要副作用**：Debian 的 `~/.bashrc` 开头有守卫 `case $- in *i*) ;; *) return;; esac`，
> 因此该 PATH **只对交互式终端有效**。
> **systemd 服务、cron 任务、`ssh 板子 iw ...` 这类非交互式 shell 都没有这个 PATH**，必须写全路径。
> 项目的变声程序以后若要做成开机自启服务，务必注意。

---

## 8. 速查

```bash
# SSH（从 Windows 笔记本）
ssh -i $HOME\.ssh\rock5b radxa@192.168.1.100

# 无线链路详情
iw dev wlP2p33s0 link
iw dev wlP2p33s0 station dump | head -n 20

# 声卡列表（编号会漂移）
cat /proc/asound/cards
```

**PowerShell 调用 SSH 的引号陷阱**：PowerShell 会剥掉远程命令串的外层双引号。
写远程命令时用**内层单引号**，例如：

```powershell
ssh -i $HOME\.ssh\rock5b radxa@192.168.1.100 "python3 -c 'print(1)'"
```
