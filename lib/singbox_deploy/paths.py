"""统一路径常量。

所有运行期产物落在仓库根目录的 state/ 下（已 gitignore）。
根目录由 singbox.sh 经环境变量 SINGBOX_DEPLOY_ROOT 传入；直接运行模块时回退到
本文件相对位置推断（lib/singbox_deploy/paths.py → 上溯两级到仓库根）。
"""

from __future__ import annotations

import os
from pathlib import Path


def _detect_root() -> Path:
    env = os.environ.get("SINGBOX_DEPLOY_ROOT")
    if env:
        return Path(env).resolve()
    # paths.py 位于 <root>/lib/singbox_deploy/paths.py
    return Path(__file__).resolve().parents[2]


ROOT = _detect_root()

# 静态资源（随仓库分发）
TEMPLATES_DIR = ROOT / "templates"

# 运行期产物
STATE_DIR = ROOT / "state"
BIN_DIR = STATE_DIR / "bin"
SINGBOX_BIN = BIN_DIR / "sing-box"
SINGBOX_VERSION_FILE = BIN_DIR / "sing-box.version"
UI_DIR = STATE_DIR / "ui"
RULESET_DIR = STATE_DIR / "ruleset"
DOWNLOADS_DIR = STATE_DIR / "downloads"
SUBSCRIPTIONS_DIR = STATE_DIR / "subscriptions"
ACTIVE_FILE = STATE_DIR / "active"
CONFIG_FILE = STATE_DIR / "config.json"
CUSTOMIZE_FILE = STATE_DIR / "customize.json"

# 系统侧目标
ETC_DIR = Path("/etc/sing-box")

# CN 规则集文件名
GEOSITE_CN = RULESET_DIR / "geosite-cn.srs"
GEOIP_CN = RULESET_DIR / "geoip-cn.srs"


def ensure_state_dirs() -> None:
    """创建所有运行期目录（幂等）。"""
    for d in (STATE_DIR, BIN_DIR, UI_DIR, RULESET_DIR, DOWNLOADS_DIR, SUBSCRIPTIONS_DIR):
        d.mkdir(parents=True, exist_ok=True)


def subscription_dir(name: str) -> Path:
    return SUBSCRIPTIONS_DIR / name
