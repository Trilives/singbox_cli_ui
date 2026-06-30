"""防火墙放行：开启局域网代理时放行混合入站端口（默认 7890/tcp+udp）。

自动探测本机防火墙工具（ufw > firewalld > nftables > iptables），以 root 增删规则。
仅依赖标准库；命令探测见 shell._have。探测不到任何工具时给出手动提示，不报错。
"""

from __future__ import annotations

from . import shell

PROXY_PORT = 7890
_PROTOCOLS = ("tcp", "udp")


def detect() -> "str | None":
    """返回当前可用的防火墙后端名；都没有则 None。"""
    if shell._have("ufw"):
        return "ufw"
    if shell._have("firewall-cmd"):
        return "firewalld"
    if shell._have("nft"):
        return "nftables"
    if shell._have("iptables"):
        return "iptables"
    return None


def _ufw(action: str, port: int) -> None:
    # action: "allow" | "delete allow"
    for proto in _PROTOCOLS:
        args = ["ufw"] + action.split() + [f"{port}/{proto}"]
        shell.run_root(args, check=False, reason="更新防火墙")


def _firewalld(add: bool, port: int) -> None:
    flag = "--add-port" if add else "--remove-port"
    for proto in _PROTOCOLS:
        shell.run_root(["firewall-cmd", "--permanent", f"{flag}={port}/{proto}"],
                       check=False, reason="更新防火墙")
    shell.run_root(["firewall-cmd", "--reload"], check=False, reason="更新防火墙")


def _iptables(add: bool, port: int, cmd: str = "iptables") -> None:
    op = "-I" if add else "-D"  # 插入到 INPUT 顶部 / 删除同一规则
    for proto in _PROTOCOLS:
        shell.run_root([cmd, op, "INPUT", "-p", proto, "--dport", str(port), "-j", "ACCEPT"],
                       check=False, reason="更新防火墙")


def _nft(add: bool, port: int) -> None:
    # nftables 无稳定的“删除某条规则”简易命令，这里仅做新增并提示
    if add:
        for proto in _PROTOCOLS:
            shell.run_root(["nft", "add", "rule", "inet", "filter", "input",
                            proto, "dport", str(port), "accept"],
                           check=False, reason="更新防火墙")
    else:
        shell.warn("nftables 规则请手动移除：nft -a list chain inet filter input 查看句柄后 delete。")


def allow(port: int = PROXY_PORT) -> bool:
    """放行端口。成功应用返回 True；无可用工具返回 False。"""
    backend = detect()
    if backend is None:
        shell.warn(f"未探测到防火墙工具，请自行确认放行 {port}/tcp,udp（或本机无防火墙）。")
        return False
    shell.info(f"经 {backend} 放行 {port}/tcp,udp …")
    _dispatch(backend, True, port)
    shell.ok(f"已放行 {port} 端口（{backend}）。")
    return True


def revoke(port: int = PROXY_PORT) -> None:
    """撤销放行（回退用）。尽力而为，失败不抛。"""
    backend = detect()
    if backend is None:
        return
    shell.info(f"经 {backend} 撤销放行 {port} …")
    _dispatch(backend, False, port)


def _dispatch(backend: str, add: bool, port: int) -> None:
    if backend == "ufw":
        _ufw("allow" if add else "delete allow", port)
    elif backend == "firewalld":
        _firewalld(add, port)
    elif backend == "nftables":
        _nft(add, port)
    elif backend == "iptables":
        _iptables(add, port)
