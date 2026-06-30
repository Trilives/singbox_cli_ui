"""把代理环境变量写入用户 ~/.bashrc（TUN 关闭时的便利项）。

TUN 关闭后是纯代理模式：内核只在 127.0.0.1:7890 提供 mixed 入站，
各程序需自行走代理。把 http_proxy/https_proxy/all_proxy 写进 bashrc，
新开的交互式 shell 即自动套用，省去逐个程序配置。

以带标记的代码块写入，便于幂等更新与卸载时整块移除。
"""

from __future__ import annotations

import os
from pathlib import Path

from . import shell

PROXY_HOST = "127.0.0.1"
PROXY_PORT = 7890

_BEGIN = "# >>> sing-box proxy env >>>"
_END = "# <<< sing-box proxy env <<<"


def _block() -> str:
    http = f"http://{PROXY_HOST}:{PROXY_PORT}"
    socks = f"socks5://{PROXY_HOST}:{PROXY_PORT}"
    return "\n".join([
        _BEGIN,
        f'export http_proxy="{http}"',
        f'export https_proxy="{http}"',
        f'export all_proxy="{socks}"',
        'export HTTP_PROXY="$http_proxy"',
        'export HTTPS_PROXY="$https_proxy"',
        'export ALL_PROXY="$all_proxy"',
        'export no_proxy="localhost,127.0.0.1,::1"',
        'export NO_PROXY="$no_proxy"',
        _END,
    ])


def target_bashrc() -> Path:
    """目标 bashrc：sudo 运行时落到真实调用用户，否则当前用户。"""
    sudo_user = os.environ.get("SUDO_USER")
    if sudo_user and sudo_user != "root":
        try:
            import pwd
            home = pwd.getpwnam(sudo_user).pw_dir
        except KeyError:
            home = os.path.expanduser("~")
    else:
        home = os.path.expanduser("~")
    return Path(home) / ".bashrc"


def _strip_block(text: str) -> str:
    """移除已存在的 sing-box 代理块，返回剩余内容。"""
    lines = text.splitlines()
    out, skip = [], False
    for ln in lines:
        if ln.strip() == _BEGIN:
            skip = True
            continue
        if ln.strip() == _END:
            skip = False
            continue
        if not skip:
            out.append(ln)
    return "\n".join(out)


def write() -> Path:
    """幂等写入代理块到 bashrc；返回写入的文件路径。"""
    path = target_bashrc()
    existed = path.exists()
    old = path.read_text("utf-8") if existed else ""
    body = _strip_block(old).rstrip("\n")
    new = (body + "\n\n" if body else "") + _block() + "\n"
    path.write_text(new, "utf-8")
    if not existed:
        _chown_to_sudo_user(path)
    shell.ok(f"已写入代理环境变量到 {path}（新开终端生效；当前终端可 `source {path}`）。")
    return path


def remove() -> None:
    """从 bashrc 移除代理块（无则跳过）。"""
    path = target_bashrc()
    if not path.exists():
        return
    old = path.read_text("utf-8")
    if _BEGIN not in old:
        return
    new = _strip_block(old).rstrip("\n") + "\n"
    path.write_text(new, "utf-8")
    shell.ok(f"已从 {path} 移除代理环境变量。")


def _chown_to_sudo_user(path: Path) -> None:
    """新建文件时若在 sudo 下，把属主还给真实用户，避免 root 占用。"""
    sudo_user = os.environ.get("SUDO_USER")
    if not sudo_user or sudo_user == "root" or os.geteuid() != 0:
        return
    try:
        import pwd
        pw = pwd.getpwnam(sudo_user)
        os.chown(path, pw.pw_uid, pw.pw_gid)
    except (KeyError, OSError):
        pass
