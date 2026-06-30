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

        # 1. 局域网下载代理（IP:端口，可留空）
        cfg = customize.load()
        proxy = menu.ask(
            "下载代理 IP:端口（出海慢时走它，如 192.168.1.10:7890），留空=保留当前/无则直连",
            default=common.strip_scheme(str(cfg.get("download_proxy") or "")),
        )
        cfg["download_proxy"] = common.normalize_proxy(proxy)
        # TUN 模式：全局透明代理（整机流量自动走代理）；关则纯代理，需各 App 自设代理
        cfg["enable_tun"] = menu.confirm(
            "启用 TUN 模式？（整机流量自动走代理；否=纯代理，需各 App 手动设代理）",
            default=bool(cfg.get("enable_tun", True)),
        )
        # 局域网代理：让局域网内其他主机把本机当作代理使用（放开 7890 监听到 0.0.0.0）
        cfg["lan_proxy"] = menu.confirm(
            "开启局域网代理？（让局域网其他主机可用本机作为代理，监听 0.0.0.0:7890）",
            default=bool(cfg.get("lan_proxy")),
        )
        t.backup_file(paths.CUSTOMIZE_FILE)
        customize.save(cfg)

        # TUN 关闭=纯代理：可选把代理变量写入 bashrc，免去逐程序设代理
        if not cfg["enable_tun"] and menu.confirm(
            "把代理环境变量写入 ~/.bashrc？（新开终端自动走 127.0.0.1:7890）", default=True
        ):
            from .. import proxyenv
            t.backup_file(proxyenv.target_bashrc())
            proxyenv.write()

        # 局域网代理需放行防火墙端口，否则其他主机连不上
        if cfg["lan_proxy"] and menu.confirm("更新防火墙放行 7890 端口？", default=True):
            from .. import firewall
            t.add_undo("撤销防火墙放行 7890", lambda: firewall.revoke(firewall.PROXY_PORT))
            firewall.allow(firewall.PROXY_PORT)

        # 2. 下载内核 + Web UI + CN 规则集
        shell.info("下载 内核 / Web UI / CN 规则集（出海慢时会用上面的代理）…")
        core.download_all()

        # 3. 增强配置（可选）——在生成订阅前配置，使首次转换即包含地区组/分流
        customize.configure_enhancements()

        # 4. 添加首个订阅（链接留空=暂不配置，直接结束初始化）
        info = common.ask_new_subscription()
        if info is None:
            shell.info(
                "已跳过订阅与服务注册，结束初始化。内核/规则已下载、设置已保存，"
                "稍后可在主菜单「订阅 → 添加订阅」补配并启动服务。"
            )
            return  # 正常返回 → 事务提交，保留步骤 1-3 成果
        name, url, source_type, cust = info
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
    if cfg.get("lan_proxy"):
        shell.info("局域网代理已开启：其他主机可设置 http/socks 代理为 本机IP:7890")
