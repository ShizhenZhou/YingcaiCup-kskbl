#!/usr/bin/env bash
# 安装/卸载 kskbl 开机自启服务
#
# 用法：
#     bash deploy/install_service.sh install     # 安装并启动
#     bash deploy/install_service.sh uninstall   # 停止并卸载
#     bash deploy/install_service.sh status      # 看状态
#     bash deploy/install_service.sh logs        # 跟踪日志
#
# 需要 sudo。

set -euo pipefail

SERVICE=kskbl
SRC_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
UNIT_SRC="$SRC_DIR/$SERVICE.service"
UNIT_DST="/etc/systemd/system/$SERVICE.service"
SUDOERS_SRC="$SRC_DIR/kskbl-sudoers"
SUDOERS_DST="/etc/sudoers.d/kskbl-shutdown"

usage() {
    echo "用法: $0 {install|uninstall|status|logs|restart}" >&2
    exit 2
}

need_root() {
    if [[ $EUID -ne 0 ]]; then
        echo "需要 sudo：sudo bash $0 $1" >&2
        exit 1
    fi
}

# 安装免密关机规则（只授权 poweroff 这一个命令）
install_sudoers() {
    if [[ ! -f $SUDOERS_SRC ]]; then
        echo "⚠️  找不到 $SUDOERS_SRC，跳过（语音关机功能会不可用）"
        return
    fi

    # visudo 在 /usr/sbin 或 /sbin，普通用户 PATH 里可能没有
    local visudo_bin=""
    for p in /usr/sbin/visudo /sbin/visudo; do
        [[ -x $p ]] && { visudo_bin=$p; break; }
    done
    if [[ -z $visudo_bin ]]; then
        echo "⚠️  找不到 visudo，跳过免密关机配置（语音关机功能会不可用）"
        return
    fi

    # 先写临时文件校验语法，避免写坏了把 sudo 弄瘫
    local tmp
    tmp=$(mktemp)
    cat "$SUDOERS_SRC" > "$tmp"
    if ! "$visudo_bin" -c -f "$tmp" >/dev/null 2>&1; then
        echo "❌ sudoers 语法校验失败，已放弃安装：" >&2
        "$visudo_bin" -c -f "$tmp" >&2 || true
        rm -f "$tmp"
        return
    fi
    install -m 440 -o root -g root "$tmp" "$SUDOERS_DST"
    rm -f "$tmp"
    echo "✅ 已安装免密关机规则：$SUDOERS_DST"
}

case "${1:-}" in
    install)
        need_root "$1"
        [[ -f $UNIT_SRC ]] || { echo "找不到 $UNIT_SRC" >&2; exit 1; }
        install -m 644 "$UNIT_SRC" "$UNIT_DST"
        install_sudoers
        systemctl daemon-reload
        systemctl enable "$SERVICE"
        systemctl restart "$SERVICE"
        sleep 2
        systemctl --no-pager status "$SERVICE" || true
        echo
        echo "✅ 已安装并启用开机自启。"
        echo "   查看日志：journalctl -u $SERVICE -f"
        echo "   临时停止：sudo systemctl stop $SERVICE"
        echo "   取消自启：sudo bash $0 uninstall"
        ;;
    uninstall)
        need_root "$1"
        systemctl disable --now "$SERVICE" 2>/dev/null || true
        rm -f "$UNIT_DST"
        rm -f "$SUDOERS_DST"
        systemctl daemon-reload
        echo "✅ 已卸载（开机不再自启，免密关机规则已删除）"
        ;;
    status)
        systemctl --no-pager status "$SERVICE" || true
        ;;
    logs)
        journalctl -u "$SERVICE" -f
        ;;
    restart)
        need_root "$1"
        systemctl restart "$SERVICE"
        systemctl --no-pager status "$SERVICE" || true
        ;;
    *)
        usage
        ;;
esac
