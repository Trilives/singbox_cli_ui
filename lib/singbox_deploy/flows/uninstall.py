"""卸载全流程：勾选式移除服务 / 自愈 / 定时器 / 产物 / 订阅。"""

from __future__ import annotations

import shutil

from .. import menu, paths, resilience, service, shell, timer


def run() -> None:
    items = [
        "systemd 服务",
        "网络自愈（NM 钩子 + watchdog）",
        "每周更新定时器",
        "清理产物（内核 / UI / 下载缓存 / 规则集）",
        "清理所有订阅与配置（含 state/）",
    ]
    try:
        chosen = menu.multiselect("卸载（勾选要移除的项）", items, default_on=(0, 1, 2))
    except menu.Cancelled:
        return
    if not chosen:
        shell.info("未选择任何项，已取消。")
        return
    shell.header("即将卸载")
    for i in chosen:
        print("  - " + items[i])
    if not menu.confirm("确认执行？", default=False):
        shell.info("已取消。")
        return

    actions = {0: _svc, 1: _resilience, 2: _timer, 3: _artifacts, 4: _state}
    for i in chosen:
        try:
            actions[i]()
        except (RuntimeError, shell.CommandError) as exc:
            shell.error(f"移除「{items[i]}」失败：{exc}")
    shell.ok("卸载流程结束。")


def _svc() -> None:
    service.remove(purge_runtime=True)


def _resilience() -> None:
    resilience.remove()


def _timer() -> None:
    timer.remove()


def _artifacts() -> None:
    for d in (paths.BIN_DIR, paths.UI_DIR, paths.DOWNLOADS_DIR, paths.RULESET_DIR):
        shutil.rmtree(d, ignore_errors=True)
    shell.ok("已清理本地产物（内核 / UI / 缓存 / 规则集）。")


def _state() -> None:
    from .. import proxyenv
    proxyenv.remove()  # 清掉写入 bashrc 的代理变量，避免残留指向失效代理
    shutil.rmtree(paths.STATE_DIR, ignore_errors=True)
    shell.ok("已清理 state/（所有订阅与配置）。")
