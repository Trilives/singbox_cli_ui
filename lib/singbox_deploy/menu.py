"""交互菜单组件：自绘 TUI（边框盒子 + 方向键高亮）。

TTY 下：方向键上下移动、反显高亮选中、边框盒子、ESC 取消。
非 TTY（管道/重定向/测试）：自动回退到编号列表 + 文本输入。

公开 API（两种模式签名一致，flows 无需关心）：
    select / multiselect / ask / confirm
取消（ESC / Ctrl-C / EOF）统一抛 Cancelled。

仅用标准库；底层按键读取见 keys.py。
"""

from __future__ import annotations

import sys
from typing import Sequence

from . import keys, shell
from .errors import Cancelled, SaveExit
from .keys import disp_width, read_line

__all__ = ["Cancelled", "SaveExit", "select", "multiselect", "ask", "confirm"]

# ANSI
_RESET = "\033[0m"
_DIM = "\033[2m"
_BOLD = "\033[1m"
_CYAN = "\033[36m"
_HIDE = "\033[?25l"
_SHOW = "\033[?25h"

_CIRCLED = "①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳"


def _num(i: int) -> str:
    return _CIRCLED[i] if i < len(_CIRCLED) else str(i + 1)


def _use_tui() -> bool:
    return keys.interactive_tty() and shell._USE_COLOR


def _row_pad(s: str, w: int) -> str:
    """补齐到宽度 w，忽略已含的 ANSI 控制码对宽度的影响。"""
    visible = _strip_ansi(s)
    return s + " " * max(0, w - disp_width(visible))


def _strip_ansi(s: str) -> str:
    import re
    return re.sub(r"\033\[[0-9;?]*[A-Za-z]", "", s)


def _max_box_width() -> int:
    """选择框内容区宽度上限：终端列数减去左右边框，避免框比终端还宽导致折行错位。"""
    import shutil
    return max(24, shutil.get_terminal_size((80, 24)).columns - 2)


def _clip(s: str, w: int) -> str:
    """按显示宽度截断到 w，超出部分用省略号收尾。"""
    if w <= 0:
        return ""
    if disp_width(s) <= w:
        return s
    limit = max(0, w - 1)
    out: list[str] = []
    width = 0
    for ch in s:
        cw = disp_width(ch)
        if width + cw > limit:
            break
        out.append(ch)
        width += cw
    return "".join(out) + "…"


class _Painter:
    """在原地重绘多行内容。"""

    def __init__(self) -> None:
        self._h = 0

    def draw(self, rows: list[str]) -> None:
        if self._h:
            sys.stdout.write(f"\033[{self._h}A")
        for r in rows:
            sys.stdout.write("\r\033[K" + r + "\n")
        sys.stdout.flush()
        self._h = len(rows)


# --------------------------------------------------------------------------- #
# select
# --------------------------------------------------------------------------- #
def select(
    title: str,
    options: Sequence[str],
    *,
    allow_back: bool = True,
    back_label: str = "返回",
    save_label: str | None = None,
    initial: int = 0,
) -> int:
    """返回选中项下标。

    两种模式：
    - 普通菜单（save_label=None）：esc = 返回（抛 Cancelled）。
    - 会话边界菜单（save_label 给定）：esc = 保存并退出（抛 SaveExit，常用、顺手）；
      组合键 Ctrl-R = 回退并退出（抛 Cancelled，少用、需慎重，避免误触丢改动）。

    `initial`：光标初始停留位置（越界则回退到 0）；配合调用方在循环里回填上次选中
    下标，可实现"返回上级菜单时光标停在原处"，而不是每次都跳回第一项。
    """
    if not _use_tui():
        return _select_plain(title, options, allow_back=allow_back,
                             back_label=back_label, save_label=save_label)

    n = len(options)
    idx = initial if 0 <= initial < n else 0
    if save_label:
        footer = f"↑/↓ 选择   ⏎ 确认   esc {save_label}   ^R {back_label}"
    else:
        footer = f"↑/↓ 选择   ⏎ 确认   esc {back_label}"
    painter = _Painter()
    sys.stdout.write(_HIDE)
    try:
        while True:
            rows = _build_select(title, options, idx, footer)
            painter.draw(rows)
            k = keys.read_key()
            if k == keys.UP:
                idx = (idx - 1) % n
            elif k == keys.DOWN:
                idx = (idx + 1) % n
            elif k == keys.ENTER:
                return idx
            elif k == keys.ESC:
                if save_label:
                    raise SaveExit()
                if allow_back:
                    raise Cancelled()
            elif save_label and k == keys.ROLLBACK:
                raise Cancelled()
            elif k.isdigit():
                j = int(k) - 1
                if 0 <= j < n:
                    idx = j
    finally:
        sys.stdout.write(_SHOW)
        sys.stdout.flush()


def _max_visible_rows() -> int:
    """可见选项行数：按终端高度自适应，预留边框/页脚/滚动提示并留底部余白。"""
    import shutil
    return max(5, shutil.get_terminal_size((80, 24)).lines - 8)


def _scroll_top(n: int, idx: int, visible: int) -> int:
    """无状态地算出滚动窗口起点，使选中项尽量居中且不越界。"""
    if n <= visible:
        return 0
    return max(0, min(idx - visible // 2, n - visible))


def _build_select(title: str, options: Sequence[str], idx: int, footer: str) -> list[str]:
    """渲染选择框；选项多于一屏时滑动显示，仅展示选中项附近的窗口。"""
    n = len(options)
    visible = min(_max_visible_rows(), n)
    top = _scroll_top(n, idx, visible)
    end = top + visible
    window = range(top, end)
    up_hint = f"  ▲ 上方还有 {top} 项" if top > 0 else ""
    down_hint = f"  ▼ 下方还有 {n - end} 项" if end < n else ""

    label = f"─ {title} "
    body = [f"  ❯ {_num(i)} {options[i]} " for i in window]
    w = min(
        max(
            [disp_width(label), disp_width(footer) + 2,
             disp_width(up_hint), disp_width(down_hint)]
            + [disp_width(v) for v in body]
        ) + 2,
        _max_box_width(),
    )

    label = _clip(label, w)
    rows = ["┌" + label + "─" * (w - disp_width(label)) + "┐"]
    rows.append("│" + _DIM + _row_pad(_clip(up_hint, w), w) + _RESET + "│")
    for i in window:
        cursor = "❯" if i == idx else " "
        text = _clip(f"  {cursor} {_num(i)} {options[i]} ", w)
        text = _CYAN + _BOLD + _row_pad(text, w) + _RESET if i == idx else _row_pad(text, w)
        rows.append("│" + text + "│")
    rows.append("│" + _DIM + _row_pad(_clip(down_hint, w), w) + _RESET + "│")
    rows.append("│" + _DIM + _row_pad(_clip("  " + footer, w), w) + _RESET + "│")
    rows.append("└" + "─" * w + "┘")
    return rows


def _select_plain(title, options, *, allow_back, back_label, save_label=None) -> int:
    shell.header(title)
    for i, opt in enumerate(options, 1):
        print(f"  {i}) {opt}")
    if save_label:
        print(f"  回车) {save_label}    r) {back_label}")
    elif allow_back:
        print(f"  0) {back_label}")
    while True:
        raw = read_line("请选择: ").strip()
        if save_label:
            if raw == "":          # 回车 = 保存并退出
                raise SaveExit()
            if raw.lower() == "r":  # r = 回退并退出
                raise Cancelled()
        elif raw in ("", "0"):
            if allow_back:
                raise Cancelled()
            continue
        if raw.isdigit() and 0 <= int(raw) - 1 < len(options):
            return int(raw) - 1
        shell.warn("无效选择，请重输。")


# --------------------------------------------------------------------------- #
# multiselect
# --------------------------------------------------------------------------- #
def multiselect(title: str, options: Sequence[str], *, default_on: Sequence[int] = ()) -> list[int]:
    if not _use_tui():
        return _multiselect_plain(title, options, default_on=default_on)

    idx = 0
    n = len(options)
    chosen = set(default_on)
    footer = "↑/↓ 移动   空格 勾选   ⏎ 确认   esc 取消"
    painter = _Painter()
    sys.stdout.write(_HIDE)
    try:
        while True:
            rows = _build_multi(title, options, idx, chosen, footer)
            painter.draw(rows)
            k = keys.read_key()
            if k == keys.UP:
                idx = (idx - 1) % n
            elif k == keys.DOWN:
                idx = (idx + 1) % n
            elif k == keys.SPACE:
                chosen.symmetric_difference_update({idx})
            elif k == keys.ENTER:
                return sorted(chosen)
            elif k == keys.ESC:
                raise Cancelled()
    finally:
        sys.stdout.write(_SHOW)
        sys.stdout.flush()


def _build_multi(title, options, idx, chosen, footer) -> list[str]:
    visibles = [f"  {'[x]' if i in chosen else '[ ]'} {opt} " for i, opt in enumerate(options)]
    label = f"─ {title} "
    w = min(
        max([disp_width(label)] + [disp_width(v) for v in visibles] + [disp_width(footer) + 2]) + 2,
        _max_box_width(),
    )
    label = _clip(label, w)
    rows = ["┌" + label + "─" * (w - disp_width(label)) + "┐", "│" + " " * w + "│"]
    for i, opt in enumerate(options):
        mark = "[x]" if i in chosen else "[ ]"
        text = _clip(f"  {mark} {opt} ", w)
        if i == idx:
            text = _CYAN + _BOLD + text + _RESET
        rows.append("│" + _row_pad(text, w) + "│")
    rows.append("│" + " " * w + "│")
    rows.append("│" + _DIM + _row_pad(_clip("  " + footer, w), w) + _RESET + "│")
    rows.append("└" + "─" * w + "┘")
    return rows


def _multiselect_plain(title, options, *, default_on) -> list[int]:
    chosen = set(default_on)
    while True:
        shell.header(title)
        for i, opt in enumerate(options, 1):
            mark = "x" if (i - 1) in chosen else " "
            print(f"  [{mark}] {i}) {opt}")
        print("  输入编号(逗号分隔)切换勾选，回车确认，q 取消")
        raw = read_line("操作: ").strip().lower()
        if raw == "":
            return sorted(chosen)
        if raw == "q":
            raise Cancelled()
        for tok in raw.replace(" ", "").split(","):
            if tok.isdigit() and 0 <= int(tok) - 1 < len(options):
                chosen.symmetric_difference_update({int(tok) - 1})


# --------------------------------------------------------------------------- #
# ask / confirm
# --------------------------------------------------------------------------- #
def ask(prompt: str, *, default: str = "", allow_empty: bool = True) -> str:
    suffix = f" [{default}]" if default else ""
    arrow = (_CYAN + "❯ " + _RESET) if _use_tui() else ""
    while True:
        raw = read_line(f"{arrow}{prompt}{suffix}: ").strip()
        if raw == "":
            if default:
                return default
            if allow_empty:
                return ""
            shell.warn("不能为空。")
            continue
        return raw


def confirm(prompt: str, *, default: bool = False) -> bool:
    suffix = " [Y/n]" if default else " [y/N]"
    arrow = (_CYAN + "❯ " + _RESET) if _use_tui() else ""
    raw = read_line(f"{arrow}{prompt}{suffix}: ").strip().lower()
    if raw == "":
        return default
    return raw in ("y", "yes", "是")
