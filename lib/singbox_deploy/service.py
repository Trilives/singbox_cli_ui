"""systemd 服务管理：在 /etc/sing-box 暂存自包含运行时并注册服务。

移植自参考仓库 setup_sing_box_service.sh 的核心思路：把二进制、配置、规则集、UI、
缓存暂存到 /etc/sing-box，并把运行时配置内的路径改写为该目录下的绝对路径，
使服务与源码目录（可能在 /home，有权限/路径耦合问题）解耦。

所有 root 操作经 shell.run_root（非 root 自动 sudo，凭证会话内缓存）。
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from . import paths, shell

DEFAULT_NAME = "sing-box"
CONFLICTING_NAME = "mihomo"


def _runtime_paths(name: str) -> dict[str, Path]:
    d = paths.ETC_DIR
    return {
        "dir": d,
        "bin": d / "sing-box",
        "config": d / f"{name}.json",
        "cache": d / f"{name}.cache.db",
        "ruleset": d / "ruleset",
        "ui": d / "ui",
        "unit": Path(f"/etc/systemd/system/{name}.service"),
    }


def _stage_runtime_config(rt: dict[str, Path]) -> Path:
    """读 state/config.json，改写为运行时绝对路径 + 启用 cache_file，写入临时文件返回。"""
    data = json.loads(paths.CONFIG_FILE.read_text("utf-8"))
    exp = data.get("experimental") or {}
    cache = exp.get("cache_file") or {}
    cache["enabled"] = True
    cache["path"] = str(rt["cache"])
    exp["cache_file"] = cache
    clash_api = exp.get("clash_api")
    if isinstance(clash_api, dict) and clash_api.get("external_ui"):
        clash_api["external_ui"] = str(rt["ui"])
    data["experimental"] = exp
    route = data.get("route")
    if isinstance(route, dict):
        for rs in route.get("rule_set") or []:
            if isinstance(rs, dict) and rs.get("type") == "local" and rs.get("path"):
                rs["path"] = str(rt["ruleset"] / Path(rs["path"]).name)
    tmp = Path(tempfile.mkstemp(prefix=".runtime-config.", suffix=".json", dir=paths.STATE_DIR)[1])
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", "utf-8")
    return tmp


def _preflight() -> None:
    if not paths.SINGBOX_BIN.exists():
        raise RuntimeError("未找到 sing-box 内核，请先执行『下载内核/UI/规则集』。")
    if not paths.CONFIG_FILE.exists():
        raise RuntimeError("未找到生效配置 state/config.json，请先添加订阅。")
    srs = list(paths.RULESET_DIR.glob("*.srs"))
    if not srs:
        raise RuntimeError("未找到 CN 规则集 (*.srs)，请先执行『下载内核/UI/规则集』。")
    if not shell._have("systemctl"):
        raise RuntimeError("未找到 systemctl，注册服务需要 systemd。")


def install(name: str = DEFAULT_NAME, *, start: bool = True) -> None:
    """注册并（可选）启动服务。会先移除同名及冲突的 mihomo 服务。"""
    _preflight()
    rt = _runtime_paths(name)
    shell.ensure_sudo("注册系统服务")

    staged = _stage_runtime_config(rt)
    try:
        shell.run_root(["mkdir", "-p", str(rt["dir"])], reason="创建运行时目录")
        shell.run_root(["chmod", "0755", str(rt["dir"])])
        # 二进制：临时名 + 原子改名，避免运行中替换报 ETXTBSY
        shell.run_root(["install", "-m", "0755", str(paths.SINGBOX_BIN), str(rt["bin"]) + ".new"])
        shell.run_root(["mv", "-f", str(rt["bin"]) + ".new", str(rt["bin"])])
        # 规则集
        shell.run_root(["rm", "-rf", str(rt["ruleset"])])
        shell.run_root(["mkdir", "-p", str(rt["ruleset"])])
        for srs in paths.RULESET_DIR.glob("*.srs"):
            shell.run_root(["install", "-m", "0644", str(srs), str(rt["ruleset"] / srs.name)])
        # UI
        if paths.UI_DIR.exists():
            shell.run_root(["rm", "-rf", str(rt["ui"])])
            shell.run_root(["cp", "-a", str(paths.UI_DIR), str(rt["ui"])])
        else:
            shell.warn("未找到 Web UI，面板将不可用；可稍后执行更新补齐。")
        # 配置
        shell.run_root(["install", "-m", "0644", str(staged), str(rt["config"])])
        shell.run_root(["install", "-m", "0644", "/dev/null", str(rt["cache"])])
        # 运行时校验
        shell.run_root([str(rt["bin"]), "check", "-c", str(rt["config"])], reason="校验配置")
    finally:
        staged.unlink(missing_ok=True)

    # 移除旧的同名 / 冲突服务
    _remove_unit(name, quiet=True)
    if name != CONFLICTING_NAME:
        _remove_unit(CONFLICTING_NAME, quiet=True)

    # 写 unit
    unit_text = (paths.TEMPLATES_DIR / "sing-box.service.tmpl").read_text("utf-8").format(
        name=name, runtime_dir=str(rt["dir"]), bin=str(rt["bin"]), config=str(rt["config"])
    )
    unit_tmp = Path(tempfile.mkstemp(prefix=".unit.", suffix=".service", dir=paths.STATE_DIR)[1])
    unit_tmp.write_text(unit_text, "utf-8")
    try:
        shell.run_root(["install", "-m", "0644", str(unit_tmp), str(rt["unit"])])
    finally:
        unit_tmp.unlink(missing_ok=True)

    shell.run_root(["systemctl", "daemon-reload"])
    shell.run_root(["systemctl", "enable", f"{name}.service"])
    if start:
        shell.run_root(["systemctl", "restart", f"{name}.service"])
        shell.ok(f"服务已启动: {name}.service")
    else:
        shell.ok(f"服务已设为开机自启（未启动）: {name}.service")


def sync_and_restart(name: str = DEFAULT_NAME) -> None:
    """把当前 state/config.json 同步到运行时并重启服务。"""
    if not is_installed(name):
        shell.warn(f"服务 {name} 未安装，跳过同步。")
        return
    _preflight()
    rt = _runtime_paths(name)
    shell.ensure_sudo("更新服务配置")
    staged = _stage_runtime_config(rt)
    try:
        shell.run_root(["install", "-m", "0644", str(staged), str(rt["config"])])
        shell.run_root([str(rt["bin"]), "check", "-c", str(rt["config"])], reason="校验配置")
    finally:
        staged.unlink(missing_ok=True)
    shell.run_root(["systemctl", "restart", f"{name}.service"])
    shell.ok(f"已同步配置并重启: {name}.service")


def remove(name: str = DEFAULT_NAME, *, purge_runtime: bool = True) -> None:
    """停止/禁用/删除服务，并清理运行时文件。"""
    shell.ensure_sudo("删除系统服务")
    _remove_unit(name)
    if purge_runtime:
        rt = _runtime_paths(name)
        shell.run_root(["rm", "-f", str(rt["config"]), str(rt["cache"])], check=False)
        # 若 /etc/sing-box 下不再有其它 <name>.json，则整目录删除
        remaining = shell.run_root(
            ["bash", "-c", f"ls {paths.ETC_DIR}/*.json 2>/dev/null | wc -l"],
            check=False, capture=True,
        )
        if (remaining.stdout or "0").strip() == "0":
            shell.run_root(["rm", "-rf", str(paths.ETC_DIR)], check=False)
    shell.ok(f"服务已删除: {name}.service")


def _remove_unit(name: str, *, quiet: bool = False) -> None:
    shell.run_root(["systemctl", "stop", f"{name}.service"], check=False, capture=quiet)
    shell.run_root(["systemctl", "disable", f"{name}.service"], check=False, capture=quiet)
    rt = _runtime_paths(name)
    shell.run_root(["rm", "-f", str(rt["unit"])], check=False)
    shell.run_root(["systemctl", "daemon-reload"], check=False, capture=quiet)
    shell.run_root(["systemctl", "reset-failed", f"{name}.service"], check=False, capture=quiet)


def is_installed(name: str = DEFAULT_NAME) -> bool:
    return _runtime_paths(name)["unit"].exists()


def status(name: str = DEFAULT_NAME) -> None:
    shell.run(["systemctl", "status", "--no-pager", f"{name}.service"], check=False)


def run(argv: list[str] | None = None) -> int:
    import argparse
    p = argparse.ArgumentParser(prog="singbox_deploy.service")
    p.add_argument("action", choices=["install", "remove", "sync", "status"])
    p.add_argument("-n", "--name", default=DEFAULT_NAME)
    p.add_argument("--no-start", action="store_true")
    args = p.parse_args(argv)
    try:
        if args.action == "install":
            install(args.name, start=not args.no_start)
        elif args.action == "remove":
            remove(args.name)
        elif args.action == "sync":
            sync_and_restart(args.name)
        else:
            status(args.name)
    except (RuntimeError, shell.CommandError) as exc:
        shell.error(str(exc))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
