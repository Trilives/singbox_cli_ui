"""拉取订阅原始内容（curl 子进程）。

机场常按 User-Agent 决定返回的订阅格式，故按来源类型设置合适的 UA。
可选经局域网 download_proxy 下载（覆盖出海慢的机场）。
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from .. import shell

# 不同来源用不同 UA，提高机场返回正确格式的概率
_USER_AGENTS = {
    "clash": "clash-verge/v2.0.0",
    "singbox": "sing-box/1.13.0",
    "base64": "v2rayN/6.0",
}

_CURL_COMMON = ["-fL", "--retry", "3", "--retry-delay", "2", "--connect-timeout", "15", "--max-time", "120"]


def fetch(url: str, *, source_type: str = "", proxy: str = "") -> bytes:
    """下载订阅内容，返回原始字节。失败抛 CommandError。"""
    ua = _USER_AGENTS.get(source_type, "Mozilla/5.0")
    with tempfile.NamedTemporaryFile(delete=False) as tf:
        out = Path(tf.name)
    try:
        args = ["curl", *_CURL_COMMON, "-A", ua]
        if proxy:
            args += ["--proxy", proxy]
        args += ["-o", str(out), url]
        shell.run(args, check=True, capture=True)
        return out.read_bytes()
    finally:
        out.unlink(missing_ok=True)
