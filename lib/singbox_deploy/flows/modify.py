"""更改配置全流程。

整个会话包在一个事务里：进入后所有配置类改动（订阅增删改 / 切换 / 刷新 /
定制层）都是临时的——选「💾 保存并退出」才提交；在主菜单按 ESC 则回退本次会话
的全部配置改动。系统类操作（更新内核 / 切换节点的实时生效 / 服务重启 / 自愈 /
定时器）为即时生效，不在会话回退范围内（菜单项以「※即时」标注）。
"""

from __future__ import annotations

from .. import core, customize, menu, paths, service, shell
from ..subscription import manager
from ..tx import Transaction
from . import common

_OPTIONS = [
    "订阅管理（增 / 删 / 改名 / 切换 / 刷新）",
    "编辑定制层（分流 / 直连 / TUN / 面板 …）",
    "切换 / 固定节点 ※即时",
    "更新 内核 / UI / 规则集 ※即时",
    "服务设置（重启 / 状态）※即时",
    "网络自愈设置 ※即时",
    "每周更新定时器 ※即时",
]


def run() -> None:
    with Transaction("更改配置") as session:
        # 会话开始即快照配置类路径，使任意改动都能被 ESC 统一回退
        for p in (paths.CONFIG_FILE, paths.ACTIVE_FILE, paths.CUSTOMIZE_FILE, paths.SUBSCRIPTIONS_DIR):
            session.snapshot(p)
        # 回退发生在文件还原之后（LIFO，最先登记 → 最后执行）：把运行中的服务对齐回退后的配置
        session.add_undo("同步服务到回退后的配置", _resync_service)

        handlers = [
            _subscriptions, _edit_customize, _node_select,
            _update_core, _service_settings, _resilience, _timer,
        ]
        while True:
            try:
                idx = menu.select(
                    "更改配置", _OPTIONS,
                    back_label="放弃本次会话改动并返回", save_label="保存并退出",
                )
            except menu.SaveExit:
                return  # 正常返回 → 事务提交
            # 主菜单 ESC：menu.Cancelled 透传到 __exit__ → 回退整个会话
            try:
                handlers[idx]()
            except menu.SaveExit:
                return  # 子菜单选了「保存并退出」→ 提交整个会话
            except menu.Cancelled:
                continue  # 单个操作中途取消 → 回主菜单，会话改动仍在缓冲中


def _resync_service() -> None:
    if service.is_installed() and manager.get_active():
        try:
            service.sync_and_restart()
        except (RuntimeError, shell.CommandError) as exc:
            shell.warn(f"服务同步失败：{exc}")


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
                "订阅操作", ["添加订阅", "切换生效订阅", "刷新订阅", "重命名", "删除订阅"],
                back_label="返回上层", save_label="保存并退出",
            )
        except menu.Cancelled:
            return  # 返回上层（改动仍在会话缓冲中）
        # SaveExit 不在此捕获：透传到 run() → 提交并退出
        try:
            (_sub_add, _sub_switch, _sub_refresh, _sub_rename, _sub_remove)[act]()
        except menu.Cancelled:
            continue


def _sub_add() -> None:
    name, url, stype, cust = common.ask_new_subscription()
    set_active = manager.get_active() is None or menu.confirm("设为生效订阅？", default=True)
    manager.add(name, url, stype, customize_flag=cust, set_active=set_active)


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
