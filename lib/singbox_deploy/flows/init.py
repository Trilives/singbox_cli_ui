"""初始化（首次部署）全流程。

整个流程包在 Transaction 内：任意步骤 ESC / 出错都会回退已应用的改动
（删除新建订阅、还原 active/config、卸载已注册服务）。
"""

from __future__ import annotations

from .. import core, customize, menu, paths, service, shell
from ..subscription import manager
from ..tx import Transaction
from . import common


def run() -> None:
    with Transaction("初始化") as t:
        shell.header("初始化（首次部署）")

        # 1. 局域网下载代理（可留空）
        cfg = customize.load()
        proxy = menu.ask(
            "局域网下载代理地址（如 http://192.168.1.10:7890，留空=直连）",
            default=str(cfg.get("download_proxy") or ""),
        )
        cfg["download_proxy"] = proxy.strip()
        t.backup_file(paths.CUSTOMIZE_FILE)
        customize.save(cfg)

        # 2. 下载内核 + Web UI + CN 规则集
        shell.info("下载 内核 / Web UI / CN 规则集（出海慢时会用上面的代理）…")
        core.download_all()

        # 3. 增强配置（可选）——在生成订阅前配置，使首次转换即包含地区组/分流
        customize.configure_enhancements()

        # 4. 添加首个订阅
        name, url, source_type, cust = common.ask_new_subscription()
        t.backup_file(paths.CONFIG_FILE)
        t.backup_file(paths.ACTIVE_FILE)
        sub = manager.add(name, url, source_type, customize_flag=cust, set_active=True)
        t.add_undo(f"删除订阅 {sub.name}", lambda: manager.remove(sub.name))

        # 5. 注册 systemd 服务
        svc = "sing-box"
        t.add_undo(f"卸载服务 {svc}", lambda: service.remove(svc, purge_runtime=True))
        start = menu.confirm("现在就启动服务？（否=仅设开机自启）", default=True)
        service.install(svc, start=start)

        # 6. 可选增强：网络自愈 / 每周更新（阶段6 接入，先占位询问）
        _optional_extras(t, svc)

        shell.ok("初始化完成。")
        _print_access_hint()


def _optional_extras(t: Transaction, svc: str) -> None:
    from .. import resilience, timer

    if menu.confirm("安装网络切换自愈？", default=True):
        t.add_undo("卸载网络自愈", lambda: resilience.remove(svc))
        resilience.install(svc)
    if menu.confirm("安装每周自动更新定时器？", default=False):
        t.add_undo("卸载每周更新", lambda: timer.remove())
        timer.install()


def _print_access_hint() -> None:
    cfg = customize.load()
    host = "0.0.0.0" if cfg.get("lan_panel") else "127.0.0.1"
    shell.info(f"Web UI: http://{host}:9090/ui")
    if host == "127.0.0.1":
        shell.info("远程查看建议用 SSH 端口转发： ssh -N -L 9090:127.0.0.1:9090 user@server")
