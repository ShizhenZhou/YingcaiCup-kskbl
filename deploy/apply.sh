#!/usr/bin/env bash
# 一键应用最新部署：重装服务 → 重启 → 确认版本
#
# 用法（需要 sudo）：
#     cd ~/proj/kskbl
#     sudo bash deploy/apply.sh
#
# 为什么需要这个：
#   改完代码只把文件传到板子上是不够的 —— systemd 里的 Python 进程
#   加载代码后不会自动重载，必须重启服务才生效。
#   这一步踩过四次坑，所以固化成脚本，顺带打印版本号方便核对。

set -euo pipefail

if [[ $EUID -ne 0 ]]; then
    echo "需要 sudo：sudo bash deploy/apply.sh" >&2
    exit 1
fi

HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
ROOT=$(cd "$HERE/.." && pwd)

echo "============================================================"
echo "  应用最新部署：$ROOT"
echo "============================================================"

# 记下代码修改时间，稍后和启动日志对比
CODE_MTIME=$(date -r "$ROOT/src/kskbl/app.py" '+%Y-%m-%d %H:%M:%S')
echo
echo "代码文件时间: $CODE_MTIME"

echo
echo "--- 重装并重启服务 ---"
bash "$HERE/install_service.sh" install

echo
echo "--- 等服务启动 ---"
sleep 4

echo
echo "============================================================"
echo "  启动日志（确认版本）"
echo "============================================================"
journalctl -u kskbl -n 15 --no-pager 2>/dev/null | tail -15 || true

echo
echo "============================================================"
VERSION_LINE=$(journalctl -u kskbl -n 30 --no-pager 2>/dev/null \
               | grep -a "代码版本" | tail -1 || true)
if [[ -n $VERSION_LINE ]]; then
    echo "  服务实际运行: $VERSION_LINE"
    echo "  代码文件时间: $CODE_MTIME"
    if [[ $VERSION_LINE == *"$CODE_MTIME"* ]]; then
        echo "  ✅ 版本一致，新代码已生效"
    else
        echo "  ⚠️  版本不一致！可能没重启成功，看上面的日志"
    fi
else
    echo "  ⚠️  没在日志里找到版本行，检查：journalctl -u kskbl -n 50"
fi
echo "============================================================"
echo
echo "查看实时日志：journalctl -u kskbl -f"
