"""网络切换自愈：NetworkManager 钩子 + systemd watchdog 定时器。

移植自参考仓库 setup_resilience.sh + sing_box_healthcheck.sh。解决 sing-box 在网卡
晚启动/掉线/漫游时卡在 "missing default interface" 的软死：
  A. NM dispatcher 钩子：真实网卡 up / 连通性变化时重启 sing-box（防抖，忽略 tun）。
  B. watchdog 定时器：周期探测，仅当「有上行但代理打不通」才重启。

healthcheck 探针装到 /etc/sing-box/healthcheck.sh（self-contained）。
"""

from __future__ import annotations

from pathlib import Path

from . import menu, paths, shell

WATCHDOG_NAME = "sing-box-watchdog"
DISPATCHER_DIR = Path("/etc/NetworkManager/dispatcher.d")
HEALTHCHECK_DEST = paths.ETC_DIR / "healthcheck.sh"


def _dispatcher_file(name: str) -> Path:
    return DISPATCHER_DIR / f"90-{name}-restart"


def _wd_service() -> Path:
    return Path(f"/etc/systemd/system/{WATCHDOG_NAME}.service")


def _wd_timer() -> Path:
    return Path(f"/etc/systemd/system/{WATCHDOG_NAME}.timer")


def _dispatcher_text(name: str, tun_dev: str, debounce: int) -> str:
    return f"""#!/usr/bin/env bash
# Auto-generated. Restart {name} when a real uplink comes up or connectivity
# changes, so auto_detect_interface re-binds. Ignores the tun device; debounced.
interface="$1"
action="$2"
[ "${{interface}}" = "{tun_dev}" ] && exit 0
case "${{action}}" in
  up|connectivity-change|dhcp4-change|dhcp6-change) ;;
  *) exit 0 ;;
esac
systemctl is-active --quiet "{name}.service" || exit 0
stamp="/run/{name}-dispatcher.last"
now="$(date +%s)"
if [ -f "${{stamp}}" ]; then
  last="$(cat "${{stamp}}" 2>/dev/null || echo 0)"
  [ "$(( now - last ))" -lt {debounce} ] && exit 0
fi
echo "${{now}}" > "${{stamp}}"
systemctl restart --no-block "{name}.service"
exit 0
"""


def _wd_service_text(name: str, tun_dev: str) -> str:
    return f"""[Unit]
Description=Probe {name} and restart it if it has soft-died ({WATCHDOG_NAME})
After={name}.service

[Service]
Type=oneshot
Environment=SERVICE_NAME={name}
Environment=TUN_DEV={tun_dev}
ExecStart={HEALTHCHECK_DEST}
"""


def _wd_timer_text(interval: str) -> str:
    return f"""[Unit]
Description=Run {WATCHDOG_NAME}.service every {interval}

[Timer]
OnBootSec=2min
OnUnitActiveSec={interval}
Unit={WATCHDOG_NAME}.service

[Install]
WantedBy=timers.target
"""


def install(name: str = "sing-box", *, interval: str = "2min", debounce: int = 20, tun_dev: str = "singbox") -> None:
    if not shell._have("systemctl"):
        raise RuntimeError("未找到 systemctl，自愈需要 systemd。")
    shell.ensure_sudo("安装网络自愈")
    src = paths.TEMPLATES_DIR / "healthcheck.sh"
    shell.run_root(["mkdir", "-p", str(paths.ETC_DIR)])
    shell.run_root(["install", "-m", "0755", str(src), str(HEALTHCHECK_DEST)])

    if DISPATCHER_DIR.is_dir():
        shell.write_root(_dispatcher_file(name), _dispatcher_text(name, tun_dev, debounce), mode="0755")
        shell.ok(f"已装 NetworkManager 钩子：{_dispatcher_file(name)}")
    else:
        shell.warn(f"{DISPATCHER_DIR} 不存在，跳过 NM 钩子（watchdog 仍兜底）。")

    shell.write_root(_wd_service(), _wd_service_text(name, tun_dev))
    shell.write_root(_wd_timer(), _wd_timer_text(interval))
    shell.run_root(["systemctl", "daemon-reload"])
    shell.run_root(["systemctl", "enable", "--now", f"{WATCHDOG_NAME}.timer"])
    shell.ok(f"网络自愈已安装（探测间隔 {interval}）。")


def remove(name: str = "sing-box") -> None:
    shell.ensure_sudo("卸载网络自愈")
    shell.run_root(["rm", "-f", str(_dispatcher_file(name))], check=False)
    for unit in (f"{WATCHDOG_NAME}.timer", f"{WATCHDOG_NAME}.service"):
        shell.run_root(["systemctl", "stop", unit], check=False, capture=True)
        shell.run_root(["systemctl", "disable", unit], check=False, capture=True)
    shell.run_root(["rm", "-f", str(_wd_timer()), str(_wd_service())], check=False)
    shell.run_root(["systemctl", "daemon-reload"], check=False)
    shell.ok("网络自愈已卸载。")


def is_installed() -> bool:
    return _wd_timer().exists()


def menu_flow() -> None:
    installed = is_installed()
    opts = (["调整探测间隔", "卸载网络自愈"] if installed else ["安装网络自愈"])
    try:
        idx = menu.select(f"网络自愈设置（当前：{'已安装' if installed else '未安装'}）", opts)
    except menu.Cancelled:
        return
    if not installed:
        install()
    elif idx == 0:
        interval = menu.ask("探测间隔（如 2min / 90s）", default="2min")
        install(interval=interval)
    else:
        remove()


def run(argv: list[str] | None = None) -> int:
    import argparse
    p = argparse.ArgumentParser(prog="singbox_deploy.resilience")
    p.add_argument("action", choices=["install", "remove"])
    p.add_argument("-n", "--name", default="sing-box")
    p.add_argument("--interval", default="2min")
    args = p.parse_args(argv)
    try:
        if args.action == "install":
            install(args.name, interval=args.interval)
        else:
            remove(args.name)
    except (RuntimeError, shell.CommandError) as exc:
        shell.error(str(exc))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
