"""极简 YAML 解析器（标准库，替代 PyYAML），仅覆盖 Clash/Mihomo 订阅会出现的结构。

支持：
- 顶层块映射；块序列(`- item`)；按缩进的嵌套块映射/序列。
- 流式集合：`{k: v, ...}` 与 `[a, b, ...]`（可嵌套，逗号在嵌套/引号内不切分）。
- 标量：单/双引号字符串、整数、浮点、true/false、null/~，其余按字符串。
- `#` 行内注释（不在引号内时）。

**不**追求通用 YAML 规范（不支持锚点/别名、多行块标量 `|`/`>`、复杂 key、日期类型等）。
目标只有一个：把 Clash 配置正确解析成 dict，使 `data["proxies"]` 是节点字典列表。
"""

from __future__ import annotations

import re
from typing import Any

_INT_RE = re.compile(r"^[+-]?[0-9]+$")
_FLOAT_RE = re.compile(r"^[+-]?(?:[0-9]*\.[0-9]+|[0-9]+\.[0-9]*)$")


class YAMLError(ValueError):
    """解析失败。"""


# --------------------------------------------------------------------------- #
# 标量
# --------------------------------------------------------------------------- #
def _parse_scalar(s: str) -> Any:
    s = s.strip()
    if s == "" or s in ("null", "Null", "NULL", "~"):
        return None
    if len(s) >= 2 and s[0] == s[-1] and s[0] in ("'", '"'):
        inner = s[1:-1]
        if s[0] == '"':
            inner = inner.replace('\\"', '"').replace("\\\\", "\\").replace("\\n", "\n")
        else:
            inner = inner.replace("''", "'")
        return inner
    if s in ("true", "True", "TRUE"):
        return True
    if s in ("false", "False", "FALSE"):
        return False
    if _INT_RE.match(s):
        try:
            return int(s)
        except ValueError:
            return s
    if _FLOAT_RE.match(s):
        try:
            return float(s)
        except ValueError:
            return s
    return s


# --------------------------------------------------------------------------- #
# 流式集合 {..} / [..]
# --------------------------------------------------------------------------- #
def _split_top(s: str) -> list[str]:
    """按逗号切分，忽略 {}/[]/引号内部的逗号。"""
    parts: list[str] = []
    depth = 0
    quote = ""
    buf: list[str] = []
    for ch in s:
        if quote:
            buf.append(ch)
            if ch == quote:
                quote = ""
            continue
        if ch in ("'", '"'):
            quote = ch
            buf.append(ch)
        elif ch in "{[":
            depth += 1
            buf.append(ch)
        elif ch in "}]":
            depth -= 1
            buf.append(ch)
        elif ch == "," and depth == 0:
            parts.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
    if buf or parts:
        parts.append("".join(buf))
    return [p for p in (p.strip() for p in parts) if p != ""]


def _split_kv(s: str) -> tuple[str, str] | None:
    """在第一个"冒号+空白/行尾"处切 key:value，忽略引号内。返回 (key, value) 或 None。"""
    quote = ""
    depth = 0
    for i, ch in enumerate(s):
        if quote:
            if ch == quote:
                quote = ""
            continue
        if ch in ("'", '"'):
            quote = ch
        elif ch in "{[":
            depth += 1
        elif ch in "}]":
            depth -= 1
        elif ch == ":" and depth == 0:
            after = s[i + 1 :]
            if after == "" or after[0] in (" ", "\t"):
                return s[:i], after.strip()
    return None


def _parse_flow(s: str) -> Any:
    s = s.strip()
    if s.startswith("{"):
        if not s.endswith("}"):
            raise YAMLError(f"流式映射未闭合: {s}")
        inner = s[1:-1].strip()
        d: dict[str, Any] = {}
        if inner:
            for item in _split_top(inner):
                kv = _split_kv(item)
                if kv is None:
                    raise YAMLError(f"流式映射条目缺少冒号: {item}")
                k, v = kv
                d[_scalar_key(k)] = _parse_flow(v)
        return d
    if s.startswith("["):
        if not s.endswith("]"):
            raise YAMLError(f"流式序列未闭合: {s}")
        inner = s[1:-1].strip()
        return [_parse_flow(x) for x in _split_top(inner)] if inner else []
    return _parse_scalar(s)


def _scalar_key(k: str) -> str:
    v = _parse_scalar(k)
    return v if isinstance(v, str) else str(v)


# --------------------------------------------------------------------------- #
# 块结构（按缩进）
# --------------------------------------------------------------------------- #
def _strip_comment(line: str) -> str:
    quote = ""
    for i, ch in enumerate(line):
        if quote:
            if ch == quote:
                quote = ""
            continue
        if ch in ("'", '"'):
            quote = ch
        elif ch == "#" and (i == 0 or line[i - 1] in (" ", "\t")):
            return line[:i]
    return line


def _logical_lines(text: str) -> list[tuple[int, str]]:
    out: list[tuple[int, str]] = []
    for raw in text.splitlines():
        line = _strip_comment(raw).rstrip()
        if line.strip() == "" or line.strip() == "---":
            continue
        indent = len(line) - len(line.lstrip(" "))
        out.append((indent, line.strip()))
    return out


def _collect_deeper(lines: list[tuple[int, str]], start: int, indent: int) -> tuple[list[tuple[int, str]], int]:
    j = start
    while j < len(lines) and lines[j][0] > indent:
        j += 1
    return lines[start:j], j


def _parse_block(lines: list[tuple[int, str]], i: int, indent: int) -> tuple[Any, int]:
    content = lines[i][1]
    if content == "-" or content.startswith("- "):
        return _parse_seq(lines, i, indent)
    return _parse_map(lines, i, indent)


def _parse_seq(lines: list[tuple[int, str]], i: int, indent: int) -> tuple[list, int]:
    items: list[Any] = []
    while i < len(lines) and lines[i][0] == indent and (lines[i][1] == "-" or lines[i][1].startswith("- ")):
        content = lines[i][1][1:].strip()
        deeper, nxt = _collect_deeper(lines, i + 1, indent)
        if content == "":
            if deeper:
                val, _ = _parse_block(deeper, 0, deeper[0][0])
            else:
                val = None
        elif content.startswith(("{", "[")):
            val = _parse_flow(content)
        elif _split_kv(content) is not None:
            # 行内以映射开头：把内联内容与更深行拼成子块
            cstart = indent + 2
            sub = [(cstart, content)] + deeper
            val, _ = _parse_block(sub, 0, cstart)
        else:
            val = _parse_scalar(content)
        items.append(val)
        i = nxt
    return items, i


def _parse_map(lines: list[tuple[int, str]], i: int, indent: int) -> tuple[dict, int]:
    d: dict[str, Any] = {}
    while i < len(lines) and lines[i][0] == indent and not (lines[i][1] == "-" or lines[i][1].startswith("- ")):
        kv = _split_kv(lines[i][1])
        if kv is None:
            raise YAMLError(f"非法映射行: {lines[i][1]}")
        key, rest = kv
        key = _scalar_key(key)
        if rest == "":
            deeper, nxt = _collect_deeper(lines, i + 1, indent)
            if deeper:
                d[key], _ = _parse_block(deeper, 0, deeper[0][0])
            else:
                d[key] = None
            i = nxt
        else:
            d[key] = _parse_flow(rest)
            i += 1
    return d, i


# --------------------------------------------------------------------------- #
# 公开接口
# --------------------------------------------------------------------------- #
def load(text: str) -> Any:
    """解析 YAML 文本，返回 dict / list / 标量。"""
    if isinstance(text, bytes):
        text = text.decode("utf-8")
    lines = _logical_lines(text)
    if not lines:
        return None
    value, _ = _parse_block(lines, 0, lines[0][0])
    return value
