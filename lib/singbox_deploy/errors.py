"""跨模块共享的异常类型（独立成模块以避免循环导入）。"""

from __future__ import annotations


class Cancelled(Exception):
    """用户主动取消：ESC / Ctrl-C / 回车留空返回 / EOF。

    在流程中向上抛出，由 Transaction 捕获并回退已应用的改动。
    """
