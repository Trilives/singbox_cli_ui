"""flows 共享的交互助手。"""

from __future__ import annotations

from .. import menu

# 菜单顺序即推荐优先级：Clash 优先
_SOURCE_OPTIONS = [
    "Clash 订阅（★推荐：本地转换、不外泄凭证）",
    "sing-box 直链（机场直接提供）",
    "通用 base64 订阅（云端解析）",
]
_SOURCE_TYPES = ["clash", "singbox", "base64"]


def ask_new_subscription() -> tuple[str, str, str, bool]:
    """交互收集新订阅信息：返回 (name, url, source_type, customize_flag)。

    任一步 ESC 抛 menu.Cancelled，由上层事务回退。
    """
    name = menu.ask("订阅名称（便于以后切换）", allow_empty=False)
    idx = menu.select("选择订阅来源类型", _SOURCE_OPTIONS, allow_back=True)
    source_type = _SOURCE_TYPES[idx]
    url = menu.ask("订阅链接", allow_empty=False)

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
