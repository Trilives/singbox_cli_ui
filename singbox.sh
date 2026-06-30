#!/usr/bin/env bash
# sing-box 部署系统 · 瘦入口
# 职责：环境检查 → 调起 Python 交互式 CLI。所有逻辑在 lib/singbox_deploy/ 内。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

err() { printf '\033[31m[错误]\033[0m %s\n' "$*" >&2; }
info() { printf '\033[36m[信息]\033[0m %s\n' "$*"; }

# --- 依赖检查 ---
missing=()
for cmd in python3 curl tar; do
  command -v "$cmd" >/dev/null 2>&1 || missing+=("$cmd")
done

if [ "${#missing[@]}" -ne 0 ]; then
  err "缺少必要命令: ${missing[*]}"
  if printf '%s\n' "${missing[@]}" | grep -qx python3; then
    info "安装 python3（按你的发行版选其一，系统源通常有国内镜像，不卡出海）："
    info "  Debian/Ubuntu: sudo apt update && sudo apt install -y python3"
    info "  Fedora/RHEL:   sudo dnf install -y python3"
    info "  Arch:          sudo pacman -S python"
  fi
  exit 1
fi

# python3 版本下限（f-string / dataclass 等）
if ! python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 8) else 1)'; then
  err "需要 python3 >= 3.8，当前 $(python3 -V 2>&1)"
  exit 1
fi

# systemd 仅在涉及服务时才必须，这里只提示
if ! command -v systemctl >/dev/null 2>&1; then
  info "未检测到 systemctl：可生成配置，但注册系统服务/自愈需要 systemd。"
fi

# --- 调起 Python CLI（免安装，PYTHONPATH 指向 lib）---
export PYTHONPATH="$ROOT/lib${PYTHONPATH:+:$PYTHONPATH}"
export SINGBOX_DEPLOY_ROOT="$ROOT"
exec python3 -m singbox_deploy "$@"
