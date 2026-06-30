"""公共工具：彩色输出、日志、子进程、root 检查。"""

from __future__ import annotations

import os
import subprocess
import sys
from typing import Sequence


# --- 彩色输出（无 TTY 时自动降级为无色）---
_USE_COLOR = sys.stdout.isatty() and os.environ.get("NO_COLOR") is None


def _c(code: str, text: str) -> str:
    if not _USE_COLOR:
        return text
    return f"\033[{code}m{text}\033[0m"


def info(msg: str) -> None:
    print(_c("36", "[信息] ") + msg)


def ok(msg: str) -> None:
    print(_c("32", "[完成] ") + msg)


def warn(msg: str) -> None:
    print(_c("33", "[注意] ") + msg)


def error(msg: str) -> None:
    print(_c("31", "[错误] ") + msg, file=sys.stderr)


def header(title: str) -> None:
    line = "─" * max(len(title), 16)
    print()
    print(_c("1", title))
    print(line)


class CommandError(RuntimeError):
    """子进程非零退出。"""

    def __init__(self, cmd: Sequence[str], returncode: int, output: str = ""):
        self.cmd = list(cmd)
        self.returncode = returncode
        self.output = output
        super().__init__(f"命令失败({returncode}): {' '.join(self.cmd)}")


def run(
    cmd: Sequence[str],
    *,
    check: bool = True,
    capture: bool = False,
    env: dict | None = None,
    cwd: str | None = None,
) -> subprocess.CompletedProcess:
    """运行子进程。check=True 时非零退出抛 CommandError。"""
    full_env = {**os.environ, **env} if env else None
    result = subprocess.run(
        list(cmd),
        text=True,
        capture_output=capture,
        env=full_env,
        cwd=cwd,
    )
    if check and result.returncode != 0:
        out = ""
        if capture:
            out = (result.stdout or "") + (result.stderr or "")
        raise CommandError(cmd, result.returncode, out)
    return result


def is_root() -> bool:
    return os.geteuid() == 0


_SUDO_OK = False


def ensure_sudo(reason: str) -> None:
    """确保后续 root 操作可执行：已是 root 则直接返回；否则用 `sudo -v` 预先取得授权。

    `sudo -v` 会交互式提示输入密码（并在会话内缓存一段时间），失败则抛 Cancelled。
    """
    global _SUDO_OK
    if is_root() or _SUDO_OK:
        return
    from .errors import Cancelled

    if not _have("sudo"):
        raise SystemExit("需要管理员权限，但未找到 sudo，请改用 `sudo ./deploy.sh` 启动。")
    info(f"{reason}需要管理员权限。")
    info("提示：也可以直接用 `sudo ./deploy.sh` 启动，避免中途输入密码。")
    rc = subprocess.run(["sudo", "-v"]).returncode
    if rc != 0:
        raise Cancelled()
    _SUDO_OK = True


def _have(cmd: str) -> bool:
    from shutil import which

    return which(cmd) is not None


def run_root(
    cmd: Sequence[str],
    *,
    check: bool = True,
    capture: bool = False,
    reason: str = "该操作",
) -> subprocess.CompletedProcess:
    """以 root 运行命令：已是 root 直接执行，否则自动加 `sudo`（先 ensure_sudo）。"""
    if is_root():
        return run(cmd, check=check, capture=capture)
    ensure_sudo(reason)
    return run(["sudo", *cmd], check=check, capture=capture)


def write_root(path, content: str, *, mode: str = "0644", reason: str = "写入系统文件") -> None:
    """以 root 把 content 写到 path（经临时文件 + install，保证权限/原子）。"""
    import tempfile

    with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as tf:
        tf.write(content)
        tmp = tf.name
    try:
        run_root(["install", "-m", mode, tmp, str(path)], reason=reason)
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass
