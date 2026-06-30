"""更改配置全流程。"""

from __future__ import annotations

from .. import core, customize, menu, paths, service, shell
from ..subscription import manager
from ..tx import Transaction
from . import common


def run() -> None:
    options = [
        "订阅管理（增 / 删 / 改名 / 切换 / 刷新）",
        "切换 / 固定节点",
        "编辑定制层（分流 / 直连 / TUN / 面板 …）",
        "更新 内核 / UI / 规则集",
        "服务设置（重启 / 状态）",
        "网络自愈设置",
        "每周更新定时器",
    ]
    handlers = [
        _subscriptions, _node_select, _edit_customize,
        _update_core, _service_settings, _resilience, _timer,
    ]
    while True:
        try:
            idx = menu.select("更改配置", options)
        except menu.Cancelled:
            return
        try:
            handlers[idx]()
        except menu.Cancelled:
            continue


# --------------------------------------------------------------------------- #
# 订阅管理
# --------------------------------------------------------------------------- #
def _subscriptions() -> None:
    while True:
        subs = manager.list_all()
        active = manager.get_active()
        active_name = active.name if active else None
        listing = [
            f"{s.name}  [{s.source_type}, {s.last_node_count} 节点]"
            + ("  ← 生效" if s.name == active_name else "")
            for s in subs
        ] or ["（暂无订阅）"]
        shell.header("订阅管理")
        for line in listing:
            print("  • " + line)
        try:
            act = menu.select(
                "订阅操作", ["添加订阅", "切换生效订阅", "刷新订阅", "重命名", "删除订阅"]
            )
        except menu.Cancelled:
            return
        try:
            (_sub_add, _sub_switch, _sub_refresh, _sub_rename, _sub_remove)[act]()
        except menu.Cancelled:
            continue


def _sub_add() -> None:
    with Transaction("添加订阅") as t:
        name, url, stype, cust = common.ask_new_subscription()
        set_active = manager.get_active() is None or menu.confirm("设为生效订阅？", default=True)
        if set_active:
            t.backup_file(paths.CONFIG_FILE)
            t.backup_file(paths.ACTIVE_FILE)
        sub = manager.add(name, url, stype, customize_flag=cust, set_active=set_active)
        t.add_undo(f"删除订阅 {sub.name}", lambda: manager.remove(sub.name))


def _pick_sub(prompt: str) -> str | None:
    subs = manager.list_all()
    if not subs:
        shell.warn("暂无订阅。")
        return None
    idx = menu.select(prompt, [s.name for s in subs])
    return subs[idx].name


def _sub_switch() -> None:
    name = _pick_sub("切换到哪个订阅")
    if name:
        with Transaction("切换订阅") as t:
            t.backup_file(paths.CONFIG_FILE)
            t.backup_file(paths.ACTIVE_FILE)
            manager.switch(name)


def _sub_refresh() -> None:
    name = _pick_sub("刷新哪个订阅")
    if name:
        manager.refresh(name)


def _sub_rename() -> None:
    name = _pick_sub("重命名哪个订阅")
    if name:
        new = menu.ask("新名称", allow_empty=False)
        manager.rename(name, new)


def _sub_remove() -> None:
    name = _pick_sub("删除哪个订阅")
    if name and menu.confirm(f"确认删除订阅「{name}」？", default=False):
        manager.remove(name)


# --------------------------------------------------------------------------- #
# 其它
# --------------------------------------------------------------------------- #
def _edit_customize() -> None:
    changed = customize.edit()
    active = manager.get_active()
    if changed and active and menu.confirm("立即重新生成生效订阅并重启？", default=True):
        with Transaction("应用定制层") as t:
            t.backup_file(paths.CONFIG_FILE)
            manager.refresh(active.name)


def _node_select() -> None:
    from .. import node_select
    node_select.select(str(paths.CONFIG_FILE))


def _update_core() -> None:
    if not menu.confirm("更新 内核 / UI / 规则集？", default=True):
        return
    core.download_all(force=True)
    active = manager.get_active()
    if active and service.is_installed():
        service.sync_and_restart()


def _service_settings() -> None:
    try:
        act = menu.select("服务设置", ["查看状态", "重启服务", "同步当前配置并重启"])
    except menu.Cancelled:
        return
    if act == 0:
        service.status()
    elif act == 1:
        shell.run_root(["systemctl", "restart", "sing-box.service"], check=False)
    else:
        service.sync_and_restart()


def _resilience() -> None:
    from .. import resilience
    resilience.menu_flow()


def _timer() -> None:
    from .. import timer
    timer.menu_flow()
