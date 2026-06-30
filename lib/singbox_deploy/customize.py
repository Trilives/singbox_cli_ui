"""定制层：state/customize.json 的默认值、模型、加载/保存、交互式编辑、增强配置闸。

迁移自参考仓库 clash_nodes_to_singbox_config.json + 脚本顶部写死的常量/命令行参数，
统一收编为一份可交互编辑的配置。转换器（subscription/convert.py）消费 CustomConfig。

字段说明见仓库根 DESIGN.md §4.3。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from . import menu, paths, shell

# --------------------------------------------------------------------------- #
# 默认值
# --------------------------------------------------------------------------- #
AI_DOMAIN_SUFFIXES = [
    "openai.com", "chatgpt.com", "oaistatic.com", "oaiusercontent.com",
    "anthropic.com", "claude.ai", "github.com", "githubusercontent.com",
    "githubassets.com", "github.io", "huggingface.co", "hf.co",
    "npmjs.com", "npmjs.org", "pypi.org", "pythonhosted.org",
    "files.pythonhosted.org", "docker.com", "docker.io", "ghcr.io",
]
STREAMING_DOMAIN_SUFFIXES = [
    "netflix.com", "nflxvideo.net", "nflximg.net", "nflxso.net",
    "disneyplus.com", "disney-plus.net", "dssott.com", "hulu.com",
    "huluim.com", "hbomax.com", "max.com", "primevideo.com",
    "amazonvideo.com", "youtube.com", "googlevideo.com", "ytimg.com",
    "spotify.com", "scdn.co",
]
LOCAL_BYPASS_DOMAINS = ["localhost"]
LOCAL_BYPASS_IP_CIDRS = ["127.0.0.0/8", "0.0.0.0/8", "::1/128"]
PRIVATE_BYPASS_IP_CIDRS = [
    "10.0.0.0/8", "192.168.0.0/16", "169.254.0.0/16", "fc00::/7", "fe80::/10",
]
OVERLAY_BYPASS_IP_CIDRS = [
    "100.64.0.0/10", "fd7a:115c:a1e0::/48", "10.126.126.0/24", "10.14.14.0/24",
]
ROUTE_EXCLUDE_IP_CIDRS = LOCAL_BYPASS_IP_CIDRS + PRIVATE_BYPASS_IP_CIDRS + OVERLAY_BYPASS_IP_CIDRS
BYPASS_PROCESS_NAMES = ["tailscale", "tailscaled"]
DEFAULT_PREFER_KEYWORDS = ["Singapore", "SG", "新加坡", "狮城"]
DEFAULT_HK_PREFER_KEYWORDS = ["Hong Kong", "HongKong", "HK", "香港"]
DEFAULT_BOOTSTRAP_DNS_SERVER = "223.5.5.5"
DEFAULT_BOOTSTRAP_DNS_PORT = 53
DEFAULT_SUBCONVERTER_BACKEND = "https://sub.v1.mk"

# customize.json 全量默认（含转换字段 + 部署字段）
DEFAULTS: dict[str, Any] = {
    "ai_domain_suffixes": AI_DOMAIN_SUFFIXES,
    "streaming_domain_suffixes": STREAMING_DOMAIN_SUFFIXES,
    "direct_domain_suffixes": [],
    "local_bypass_domains": LOCAL_BYPASS_DOMAINS,
    "route_exclude_ip_cidrs": ROUTE_EXCLUDE_IP_CIDRS,
    "bypass_process_names": BYPASS_PROCESS_NAMES,
    "tun_exclude_uids": [],
    "lan_panel": False,
    "lan_proxy": False,
    "bootstrap_dns_server": DEFAULT_BOOTSTRAP_DNS_SERVER,
    "bootstrap_dns_port": DEFAULT_BOOTSTRAP_DNS_PORT,
    # 增强：地区组
    "generate_sg_groups": False,
    "generate_hk_groups": False,
    "prefer_keywords": DEFAULT_PREFER_KEYWORDS,
    "hk_prefer_keywords": DEFAULT_HK_PREFER_KEYWORDS,
    "default_outbound": "Proxy",
    # 部署侧
    "subconverter_backend": DEFAULT_SUBCONVERTER_BACKEND,
    "base64_local_fallback": False,
    "github_mirror": "",
    "download_proxy": "",
}


# --------------------------------------------------------------------------- #
# 转换器消费的模型
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class CustomConfig:
    ai_domain_suffixes: list[str]
    streaming_domain_suffixes: list[str]
    direct_domain_suffixes: list[str]
    local_bypass_domains: list[str]
    route_exclude_ip_cidrs: list[str]
    bypass_process_names: list[str]
    tun_exclude_uids: list[int]
    lan_panel: bool
    lan_proxy: bool
    bootstrap_dns_server: str | None
    bootstrap_dns_port: int
    prefer_keywords: list[str] = field(default_factory=lambda: list(DEFAULT_PREFER_KEYWORDS))
    hk_prefer_keywords: list[str] = field(default_factory=lambda: list(DEFAULT_HK_PREFER_KEYWORDS))
    generate_sg_groups: bool = False
    generate_hk_groups: bool = False
    default_outbound: str = "Proxy"


def to_custom_config(cfg: dict[str, Any]) -> CustomConfig:
    """从 customize.json dict 构造转换器用的 CustomConfig（缺字段补默认）。"""
    g = lambda k: cfg.get(k, DEFAULTS[k])  # noqa: E731
    return CustomConfig(
        ai_domain_suffixes=list(g("ai_domain_suffixes")) or AI_DOMAIN_SUFFIXES,
        streaming_domain_suffixes=list(g("streaming_domain_suffixes")) or STREAMING_DOMAIN_SUFFIXES,
        direct_domain_suffixes=list(g("direct_domain_suffixes")),
        local_bypass_domains=list(g("local_bypass_domains")) or LOCAL_BYPASS_DOMAINS,
        route_exclude_ip_cidrs=list(g("route_exclude_ip_cidrs")) or ROUTE_EXCLUDE_IP_CIDRS,
        bypass_process_names=list(g("bypass_process_names")),
        tun_exclude_uids=[int(x) for x in g("tun_exclude_uids")],
        lan_panel=bool(g("lan_panel")),
        lan_proxy=bool(g("lan_proxy")),
        bootstrap_dns_server=g("bootstrap_dns_server"),
        bootstrap_dns_port=int(g("bootstrap_dns_port")),
        prefer_keywords=list(g("prefer_keywords")),
        hk_prefer_keywords=list(g("hk_prefer_keywords")),
        generate_sg_groups=bool(g("generate_sg_groups")),
        generate_hk_groups=bool(g("generate_hk_groups")),
        default_outbound=str(g("default_outbound")),
    )


# --------------------------------------------------------------------------- #
# 加载 / 保存
# --------------------------------------------------------------------------- #
def load() -> dict[str, Any]:
    """读 customize.json，缺失字段以默认补全。"""
    data: dict[str, Any] = {}
    if paths.CUSTOMIZE_FILE.exists():
        try:
            data = json.loads(paths.CUSTOMIZE_FILE.read_text("utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            shell.warn(f"customize.json 读取失败，使用默认值：{exc}")
            data = {}
    merged = dict(DEFAULTS)
    if isinstance(data, dict):
        merged.update({k: v for k, v in data.items() if k in DEFAULTS})
    return merged


def save(cfg: dict[str, Any]) -> None:
    paths.ensure_state_dirs()
    paths.CUSTOMIZE_FILE.write_text(
        json.dumps(cfg, indent=2, ensure_ascii=False) + "\n", "utf-8"
    )


def ensure_exists() -> dict[str, Any]:
    """首次运行时落地默认 customize.json。"""
    cfg = load()
    if not paths.CUSTOMIZE_FILE.exists():
        save(cfg)
    return cfg


# --------------------------------------------------------------------------- #
# 交互式编辑（见 DESIGN §5.5）
# --------------------------------------------------------------------------- #
_LIST_FIELDS = {
    "ai_domain_suffixes": "AI 域名后缀",
    "streaming_domain_suffixes": "流媒体域名后缀",
    "direct_domain_suffixes": "直连域名后缀",
    "local_bypass_domains": "本地直连域名",
    "route_exclude_ip_cidrs": "TUN 排除网段",
    "bypass_process_names": "直连进程名",
    "tun_exclude_uids": "TUN 排除 UID",
    "prefer_keywords": "新加坡关键词",
    "hk_prefer_keywords": "香港关键词",
}
_BOOL_FIELDS = {
    "lan_panel": "LAN 面板暴露",
    "lan_proxy": "局域网代理（其他主机可用本机代理）",
    "generate_sg_groups": "生成新加坡地区组",
    "generate_hk_groups": "生成香港地区组",
    "base64_local_fallback": "base64 应急本地解析",
}
_SCALAR_FIELDS = {
    "bootstrap_dns_server": "引导 DNS 服务器",
    "bootstrap_dns_port": "引导 DNS 端口",
    "default_outbound": "默认主出站",
    "subconverter_backend": "subconverter 后端",
    "github_mirror": "GitHub 加速前缀",
    "download_proxy": "下载代理",
}


def _summary(cfg: dict[str, Any], key: str) -> str:
    v = cfg.get(key, DEFAULTS.get(key))
    if isinstance(v, list):
        return f"{len(v)} 条" if v else "空"
    if isinstance(v, bool):
        return "开" if v else "关"
    return "未设置" if v in ("", None) else str(v)


def _edit_labels(cfg: dict[str, Any]) -> list[str]:
    return (
        [f"{_LIST_FIELDS[k]}（{_summary(cfg, k)}）" for k in _LIST_FIELDS]
        + [f"{_BOOL_FIELDS[k]}：{_summary(cfg, k)}" for k in _BOOL_FIELDS]
        + [f"{_SCALAR_FIELDS[k]}：{_summary(cfg, k)}" for k in _SCALAR_FIELDS]
    )


def edit() -> bool:
    """交互式编辑 customize.json（缓冲式）。

    「保存并退出」才写盘；ESC = 放弃本次全部改动。返回是否实际保存了改动。
    """
    original = load()
    cfg = json.loads(json.dumps(original))  # 工作副本
    changed = False
    field_keys = list(_LIST_FIELDS) + list(_BOOL_FIELDS) + list(_SCALAR_FIELDS)
    while True:
        try:
            idx = menu.select(
                "编辑定制层", _edit_labels(cfg),
                back_label="放弃修改并退出", save_label="保存并退出",
            )
        except menu.SaveExit:
            if not changed:
                shell.info("未做修改。")
                return False
            save(cfg)
            shell.ok("定制层已保存。")
            _sync_lan_proxy_firewall(original, cfg)
            return True
        except menu.Cancelled:
            if changed:
                shell.warn("已放弃本次修改（未写盘）。")
            return False
        key = field_keys[idx]
        if key in _LIST_FIELDS:
            changed |= _edit_list(cfg, key, _LIST_FIELDS[key])
        elif key in _BOOL_FIELDS:
            cfg[key] = not bool(cfg.get(key))
            changed = True
        else:
            changed |= _edit_scalar(cfg, key, _SCALAR_FIELDS[key])


def _sync_lan_proxy_firewall(original: dict[str, Any], cfg: dict[str, Any]) -> None:
    """lan_proxy 开关变化时，按需更新防火墙放行 7890 端口。"""
    before, after = bool(original.get("lan_proxy")), bool(cfg.get("lan_proxy"))
    if before == after:
        return
    from . import firewall
    if after:
        if menu.confirm("已开启局域网代理，更新防火墙放行 7890 端口？", default=True):
            firewall.allow(firewall.PROXY_PORT)
    else:
        if menu.confirm("已关闭局域网代理，撤销防火墙放行 7890 端口？", default=True):
            firewall.revoke(firewall.PROXY_PORT)


def _edit_list(cfg: dict[str, Any], key: str, label: str) -> bool:
    is_int = key == "tun_exclude_uids"
    changed = False
    while True:
        items = list(cfg.get(key, []))
        shell.info(f"{label}：当前 {len(items)} 条" + (("：" + ", ".join(str(x) for x in items)) if items else ""))
        try:
            act = menu.select(
                f"编辑 · {label}",
                ["添加一条", "删除一条", "批量粘贴替换（逗号/空格分隔）", "恢复默认", "清空"],
            )
        except menu.Cancelled:
            return changed
        try:
            if act == 0:
                val = menu.ask("新增值", allow_empty=False)
                items.append(int(val) if is_int else val)
            elif act == 1:
                if not items:
                    continue
                di = menu.select("删除哪一条", [str(x) for x in items])
                items.pop(di)
            elif act == 2:
                raw = menu.ask("粘贴（逗号或空格分隔）", allow_empty=True)
                toks = [t for t in raw.replace(",", " ").split() if t]
                items = [int(t) for t in toks] if is_int else toks
            elif act == 3:
                items = list(DEFAULTS.get(key, []))
            elif act == 4:
                items = []
        except (ValueError, menu.Cancelled):
            shell.warn("输入无效，已跳过。")
            continue
        cfg[key] = items
        changed = True


def _edit_scalar(cfg: dict[str, Any], key: str, label: str) -> bool:
    cur = str(cfg.get(key, "") or "")
    try:
        val = menu.ask(f"{label}（留空清除）", default=cur, allow_empty=True)
    except menu.Cancelled:
        return False
    if key == "bootstrap_dns_port":
        try:
            cfg[key] = int(val)
        except ValueError:
            shell.warn("端口需为整数，未修改。")
            return False
    else:
        cfg[key] = val
    return True


# --------------------------------------------------------------------------- #
# 增强配置闸（初始化流程调用）
# --------------------------------------------------------------------------- #
def configure_enhancements() -> None:
    """交互询问是否启用增强配置；启用则配置地区组等。"""
    cfg = load()
    if not menu.confirm("是否启用增强配置（地区分组 / AI·流媒体分流 / 直连进程等）？", default=False):
        # 关闭地区组，保持通用配置
        cfg["generate_sg_groups"] = False
        cfg["generate_hk_groups"] = False
        save(cfg)
        return
    # 地区组
    if menu.confirm("启用「新加坡」地区组（SG-Auto/SG-Fallback）？", default=False):
        cfg["generate_sg_groups"] = True
        raw = menu.ask("新加坡匹配关键词（逗号分隔）", default=",".join(cfg.get("prefer_keywords", [])))
        cfg["prefer_keywords"] = [t.strip() for t in raw.split(",") if t.strip()]
    else:
        cfg["generate_sg_groups"] = False
    if menu.confirm("启用「香港」地区组（HK-Auto/HK-Fallback）？", default=False):
        cfg["generate_hk_groups"] = True
        raw = menu.ask("香港匹配关键词（逗号分隔）", default=",".join(cfg.get("hk_prefer_keywords", [])))
        cfg["hk_prefer_keywords"] = [t.strip() for t in raw.split(",") if t.strip()]
    else:
        cfg["generate_hk_groups"] = False
    save(cfg)
    shell.ok("增强配置已保存。")
    if menu.confirm("现在进一步细调分流/直连等字段？", default=False):
        edit()


# --------------------------------------------------------------------------- #
# 独立调用
# --------------------------------------------------------------------------- #
def run(argv: list[str] | None = None) -> int:
    ensure_exists()
    edit()
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
