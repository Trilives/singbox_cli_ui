"""可中断的终端输入：支持 ESC 受控取消。

- TTY 下用 termios 原始模式逐键读取：单独 ESC → 取消；方向键等转义序列(\x1b[..)忽略；
  Ctrl-C → 取消；退格可删（正确处理中文等宽字符宽度）；回车提交。
- 非 TTY（管道/重定向，如自动化测试）回退到标准 input()，EOF 视为取消。

只用标准库 termios / tty / select / unicodedata。
"""

from __future__ import annotations

import os
import sys
import unicodedata

from .errors import Cancelled

try:
    import termios
    import tty
    import select as _select
    _HAS_TERMIOS = True
except ImportError:  # 非 POSIX 平台
    termios = tty = _select = None  # type: ignore[assignment]
    _HAS_TERMIOS = False


def interactive_tty() -> bool:
    return _HAS_TERMIOS and sys.stdin.isatty() and sys.stdout.isatty()


# 兼容旧内部名
_interactive_tty = interactive_tty


def _char_width(ch: str) -> int:
    return 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1


def disp_width(s: str) -> int:
    """字符串的终端显示宽度（中文/全角算 2）。"""
    return sum(_char_width(c) for c in s)


# 逻辑按键常量
UP, DOWN, LEFT, RIGHT = "UP", "DOWN", "LEFT", "RIGHT"
ENTER, ESC, SPACE, BACKSPACE = "ENTER", "ESC", "SPACE", "BACKSPACE"

_ARROW = {"[A": UP, "[B": DOWN, "[C": RIGHT, "[D": LEFT, "OA": UP, "OB": DOWN}


def read_key() -> str:
    """读取一个逻辑按键（仅在 TTY 下调用）：方向键 / ENTER / ESC / SPACE / 字符。"""
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setcbreak(fd)
        b = os.read(fd, 1)
        if not b:
            return ESC
        o = b[0]
        if o == 3:  # Ctrl-C 视为取消
            return ESC
        if o in (10, 13):
            return ENTER
        if o == 32:
            return SPACE
        if o in (127, 8):
            return BACKSPACE
        if o == 27:  # ESC 或方向键转义序列
            r, _, _ = _select.select([fd], [], [], 0.05)
            if not r:
                return ESC
            seq = os.read(fd, 2).decode("latin-1", "ignore")
            return _ARROW.get(seq, ESC)
        if o < 0x20:
            return read_key()  # 其他控制字符忽略，继续读
        return _decode_char(fd, b)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


def _decode_char(fd: int, first: bytes) -> str:
    """给定首字节，补齐并解码一个完整 UTF-8 字符。"""
    o = first[0]
    if o < 0x80:
        return first.decode("latin-1")
    if o >= 0xF0:
        n = 3
    elif o >= 0xE0:
        n = 2
    elif o >= 0xC0:
        n = 1
    else:
        return ""
    return (first + os.read(fd, n)).decode("utf-8", "ignore")


def read_line(prompt: str = "") -> str:
    """读取一行；ESC / Ctrl-C / EOF 抛 Cancelled。"""
    if not _interactive_tty():
        try:
            return input(prompt)
        except (EOFError, KeyboardInterrupt):
            print()
            raise Cancelled()

    sys.stdout.write(prompt)
    sys.stdout.flush()
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    buf: list[str] = []
    try:
        tty.setcbreak(fd)
        while True:
            b = os.read(fd, 1)
            if not b:
                print()
                raise Cancelled()
            o = b[0]
            if o == 3:  # Ctrl-C
                print()
                raise Cancelled()
            if o in (10, 13):  # 回车
                sys.stdout.write("\n")
                sys.stdout.flush()
                return "".join(buf)
            if o == 27:  # ESC：区分单独 ESC 与转义序列
                r, _, _ = _select.select([fd], [], [], 0.05)
                if r:
                    os.read(fd, 8)  # 吞掉方向键等序列，忽略
                    continue
                print()
                raise Cancelled()
            if o in (127, 8):  # 退格
                if buf:
                    ch = buf.pop()
                    w = _char_width(ch)
                    sys.stdout.write("\b" * w + " " * w + "\b" * w)
                    sys.stdout.flush()
                continue
            # 普通字符（含多字节）
            if o < 0x20:
                continue  # 其他控制字符忽略
            ch = _decode_char(fd, b)
            if ch:
                buf.append(ch)
                sys.stdout.write(ch)
                sys.stdout.flush()
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)
