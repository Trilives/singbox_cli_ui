"""来源类型识别：校验拉取内容与用户所选类型是否相符（也支持启发式判断）。"""

from __future__ import annotations

import base64
import json

from .. import yamlmini


def detect(raw: bytes) -> str:
    """启发式判断订阅类型：返回 clash | singbox | base64 | unknown。"""
    text = raw.decode("utf-8", "ignore").strip()
    if not text:
        return "unknown"

    # singbox：合法 JSON 且含 outbounds/inbounds
    try:
        data = json.loads(text)
        if isinstance(data, dict) and ("outbounds" in data or "inbounds" in data):
            return "singbox"
    except json.JSONDecodeError:
        pass

    # clash：YAML 且含 proxies 列表
    if "proxies:" in text or "proxy-groups:" in text:
        try:
            d = yamlmini.load(text)
            if isinstance(d, dict) and isinstance(d.get("proxies"), list):
                return "clash"
        except yamlmini.YAMLError:
            pass

    # base64：可解码且含节点分享链接
    if _looks_base64(text):
        try:
            decoded = base64.b64decode(text + "=" * (-len(text) % 4)).decode("utf-8", "ignore")
            if "://" in decoded:
                return "base64"
        except (ValueError, base64.binascii.Error):  # type: ignore[attr-defined]
            pass

    return "unknown"


def _looks_base64(text: str) -> bool:
    sample = "".join(text.split())
    if len(sample) < 16:
        return False
    allowed = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/=-_")
    return all(c in allowed for c in sample)


def warn_if_mismatch(declared: str, raw: bytes) -> str | None:
    """若检测类型与声明不符，返回提示文本；相符或无法判断返回 None。"""
    found = detect(raw)
    if found != "unknown" and found != declared:
        return f"内容看起来更像「{found}」而非你选择的「{declared}」。"
    return None
