"""跨模块共享的异常类型（独立成模块以避免循环导入）。"""

from __future__ import annotations


class Cancelled(Exception):
    """用户主动取消：ESC / Ctrl-C / 回车留空返回 / EOF。

    在流程中向上抛出，由 Transaction 捕获并回退已应用的改动。
    """


class SaveExit(Exception):
    """用户在菜单中选择「保存并退出」：保留并提交本层改动后退出。

    与 Cancelled 相对：Cancelled = 放弃/回退；SaveExit = 保存/提交。
    """
