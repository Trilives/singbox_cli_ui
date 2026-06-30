"""每周自动更新定时器：周期性更新 内核/UI/规则集并同步重启服务。

移植自参考仓库 setup_weekly_update_timer.sh + update_and_redeploy.sh。
ExecStart 调用本项目入口的 `update` 子命令（core 全量更新 + service 同步）。
"""

from __future__ import annotations

from pathlib import Path

from . import menu, paths, shell

TIMER_NAME = "sing-box-update"
DEFAULT_ONCALENDAR = "Mon *-*-* 03:00:00"
DEFAULT_DELAY = "30min"


def _service_file() -> Path:
    return Path(f"/etc/systemd/system/{TIMER_NAME}.service")


def _timer_file() -> Path:
    return Path(f"/etc/systemd/system/{TIMER_NAME}.timer")


def _exec_start() -> str:
    return f"{paths.ROOT / 'singbox.sh'} update"


def _service_text() -> str:
    return f"""[Unit]
Description=Weekly sing-box core, UI, and rule-set update ({TIMER_NAME})
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
ExecStart={_exec_start()}
"""


def _timer_text(on_calendar: str, delay: str) -> str:
    return f"""[Unit]
Description=Run {TIMER_NAME}.service weekly

[Timer]
OnCalendar={on_calendar}
RandomizedDelaySec={delay}
Persistent=true
Unit={TIMER_NAME}.service

[Install]
WantedBy=timers.target
"""


def install(*, on_calendar: str = DEFAULT_ONCALENDAR, delay: str = DEFAULT_DELAY) -> None:
    if not shell._have("systemctl"):
        raise RuntimeError("未找到 systemctl，定时器需要 systemd。")
    shell.ensure_sudo("安装每周更新定时器")
    shell.write_root(_service_file(), _service_text())
    shell.write_root(_timer_file(), _timer_text(on_calendar, delay))
    shell.run_root(["systemctl", "daemon-reload"])
    shell.run_root(["systemctl", "enable", "--now", f"{TIMER_NAME}.timer"])
    shell.ok(f"每周更新定时器已安装（{on_calendar}）。")


def remove() -> None:
    shell.ensure_sudo("卸载每周更新定时器")
    for unit in (f"{TIMER_NAME}.timer", f"{TIMER_NAME}.service"):
        shell.run_root(["systemctl", "stop", unit], check=False, capture=True)
        shell.run_root(["systemctl", "disable", unit], check=False, capture=True)
    shell.run_root(["rm", "-f", str(_timer_file()), str(_service_file())], check=False)
    shell.run_root(["systemctl", "daemon-reload"], check=False)
    shell.ok("每周更新定时器已卸载。")


def is_installed() -> bool:
    return _timer_file().exists()


def menu_flow() -> None:
    installed = is_installed()
    opts = (["改时间表", "卸载定时器"] if installed else ["安装每周更新定时器"])
    try:
        idx = menu.select(f"每周更新定时器（当前：{'已安装' if installed else '未安装'}）", opts)
    except menu.Cancelled:
        return
    if not installed:
        install()
    elif idx == 0:
        cal = menu.ask("OnCalendar 表达式", default=DEFAULT_ONCALENDAR)
        install(on_calendar=cal)
    else:
        remove()


def run(argv: list[str] | None = None) -> int:
    import argparse
    p = argparse.ArgumentParser(prog="singbox_deploy.timer")
    p.add_argument("action", choices=["install", "remove"])
    args = p.parse_args(argv)
    try:
        install() if args.action == "install" else remove()
    except (RuntimeError, shell.CommandError) as exc:
        shell.error(str(exc))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
