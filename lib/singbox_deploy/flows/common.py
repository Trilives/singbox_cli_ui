"""flows 共享的交互助手。"""

from __future__ import annotations

import time

from .. import menu

# 菜单顺序即推荐优先级：Clash 优先
_SOURCE_OPTIONS = [
    "Clash 订阅（★推荐：本地转换、不外泄凭证）",
    "sing-box 直链（机场直接提供）",
    "通用 base64 订阅（云端解析）",
]
_SOURCE_TYPES = ["clash", "singbox", "base64"]


def strip_scheme(proxy: str) -> str:
    """去掉 http:// / https:// 前缀，便于以 IP:端口 形式回显默认值。"""
    p = proxy.strip()
    return p.split("://", 1)[1] if "://" in p else p


def normalize_proxy(raw: str) -> str:
    """把用户输入的代理归一化为可用 URL：空→空；含 scheme 原样；否则补 http://。"""
    p = raw.strip()
    if not p:
        return ""
    if "://" in p:
        return p
    return "http://" + p


def ask_new_subscription() -> tuple[str, str, str, bool] | None:
    """交互收集新订阅信息：返回 (name, url, source_type, customize_flag)。

    订阅链接留空 → 返回 None，表示"暂不配置订阅"（由上层决定结束初始化 / 取消添加）。
    任一步 ESC 抛 menu.Cancelled，由上层事务回退。
    """
    default_name = time.strftime("sub-%Y%m%d-%H%M%S")
    name = menu.ask("订阅名称，留空=时间戳", default=default_name)
    idx = menu.select("选择订阅来源类型", _SOURCE_OPTIONS, allow_back=True)
    source_type = _SOURCE_TYPES[idx]
    url = menu.ask("订阅链接，留空=暂不配置", allow_empty=True)
    if not url:
        return None

    if source_type == "singbox":
        customize_flag = menu.confirm(
            "是否注入定制层（分流 / TUN 排除 / 本地规则集 / 面板）？\n"
            "  选否则尽量保留机场原配置。",
            default=True,
        )
    else:
        # clash / base64 始终经本地转换器生成，定制层默认应用
        customize_flag = True
    return name, url, source_type, customize_flag
