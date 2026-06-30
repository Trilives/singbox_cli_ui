"""订阅 → sing-box 配置转换。

忠实移植参考仓库 clash_nodes_to_singbox.py 的协议转换与分组/路由/DNS 构建，改动：
- YAML 解析 yaml.safe_load → yamlmini.load（零依赖）。
- 写死的 GENERATE_SG/HK 常量、--prefer/--hk-prefer/--default-outbound 命令行参数，
  统一改由 CustomConfig（customize.json）驱动。
- rule_set 与 external_ui 改用 state/ 下绝对路径，部署到 /etc 后仍能定位。

支持协议：anytls / trojan / ss / vmess / vless / hysteria2 / tuic / socks / http。
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from .. import paths, yamlmini
from ..customize import CustomConfig, to_custom_config

RESERVED_TAGS = {
    "Proxy", "AI", "Streaming", "Direct", "Auto",
    "SG-Auto", "SG-Fallback", "HK-Auto", "HK-Fallback",
    "Fallback", "DIRECT", "BLOCK", "DNS",
}
DIRECT_GROUP_TAG = "Direct"
DEFAULT_OUTBOUND_CHOICES = ("Proxy", "Auto", "AI", "SG-Auto", "SG-Fallback", "HK-Auto", "HK-Fallback")
SG_EXCLUDE_KEYWORDS = ("实验",)
HK_EXCLUDE_KEYWORDS = ("实验",)
INFO_NODE_PREFIXES = ("traffic:", "expire:", "剩余流量", "过期时间")
GEOSITE_CN_RULE_SET_TAG = "geosite-cn"
GEOIP_CN_RULE_SET_TAG = "geoip-cn"
BOOTSTRAP_DNS_TAG = "dns-direct"
REMOTE_DNS_TAG = "dns-proxy"
BOOTSTRAP_DNS_DHCP = "dhcp"
BOOTSTRAP_DOMAIN_RESOLVER = {"server": BOOTSTRAP_DNS_TAG, "strategy": "prefer_ipv4"}


class ConversionError(Exception):
    pass


# --------------------------------------------------------------------------- #
# 基础工具
# --------------------------------------------------------------------------- #
def normalize_port(value: Any) -> int | None:
    try:
        port = int(value)
    except (TypeError, ValueError):
        return None
    return port if 1 <= port <= 65535 else None


def make_safe_tag(name: str, used_tags: set[str]) -> str:
    base = str(name).strip() or "node"
    tag = base
    index = 1
    while tag in used_tags or tag in RESERVED_TAGS:
        tag = f"{base}-{index}"
        index += 1
    used_tags.add(tag)
    return tag


def is_preferred_node(name: str, prefer_keywords: list[str]) -> bool:
    lowered = name.lower()
    return any(k.lower() in lowered for k in prefer_keywords)


def is_excluded_sg_node(name: str) -> bool:
    return any(k in name for k in SG_EXCLUDE_KEYWORDS)


def is_excluded_hk_node(name: str) -> bool:
    return any(k in name for k in HK_EXCLUDE_KEYWORDS)


def is_informational_node(name: str) -> bool:
    lowered = name.strip().lower()
    return any(lowered.startswith(p) for p in INFO_NODE_PREFIXES)


def parse_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def require_fields(proxy: dict[str, Any], fields: list[str]) -> str | None:
    for f in fields:
        if proxy.get(f) in (None, ""):
            return f"missing {f}"
    if normalize_port(proxy.get("port", proxy.get("server_port"))) is None:
        return "missing or invalid port"
    return None


def base_outbound(proxy: dict[str, Any], tag: str, outbound_type: str) -> dict[str, Any]:
    server = proxy.get("server")
    port = normalize_port(proxy.get("server_port", proxy.get("port")))
    if not server:
        raise ConversionError("missing server")
    if port is None:
        raise ConversionError("missing or invalid port")
    return {"type": outbound_type, "tag": tag, "server": str(server), "server_port": port}


# --------------------------------------------------------------------------- #
# TLS / transport
# --------------------------------------------------------------------------- #
def tls_config(proxy: dict[str, Any], default_enabled: bool = False) -> dict[str, Any] | None:
    enabled = parse_bool(proxy.get("tls", default_enabled))
    server_name = proxy.get("servername") or proxy.get("server_name") or proxy.get("sni")
    insecure = proxy.get("skip-cert-verify")
    alpn = proxy.get("alpn")
    fingerprint = proxy.get("client-fingerprint") or proxy.get("fingerprint")
    certificate_path = proxy.get("ca") or proxy.get("certificate_path")
    certificate = proxy.get("ca-str") or proxy.get("certificate")
    client_certificate_path = proxy.get("client-cert") or proxy.get("client_certificate_path")
    client_certificate = proxy.get("client-cert-str") or proxy.get("client_certificate")
    client_key_path = proxy.get("client-key") or proxy.get("client_key_path")
    client_key = proxy.get("client-key-str") or proxy.get("client_key")

    if (
        not enabled and not server_name and insecure is None and not alpn and not fingerprint
        and not certificate_path and not certificate and not client_certificate_path
        and not client_certificate and not client_key_path and not client_key
    ):
        return None

    tls: dict[str, Any] = {
        "enabled": enabled or bool(
            server_name or alpn or fingerprint or certificate_path or certificate
            or client_certificate_path or client_certificate or client_key_path or client_key
        )
    }
    if server_name:
        tls["server_name"] = str(server_name)
    if insecure is not None:
        tls["insecure"] = parse_bool(insecure)
    if isinstance(alpn, list):
        tls["alpn"] = [str(x) for x in alpn]
    elif isinstance(alpn, str) and alpn:
        tls["alpn"] = [x.strip() for x in alpn.split(",") if x.strip()]
    if fingerprint:
        tls["utls"] = {"enabled": True, "fingerprint": str(fingerprint)}
    if certificate_path:
        tls["certificate_path"] = str(certificate_path)
    if certificate:
        tls["certificate"] = str(certificate)
    if client_certificate_path:
        tls["client_certificate_path"] = str(client_certificate_path)
    if client_certificate:
        tls["client_certificate"] = str(client_certificate)
    if client_key_path:
        tls["client_key_path"] = str(client_key_path)
    if client_key:
        tls["client_key"] = str(client_key)
    return tls


def websocket_transport(proxy: dict[str, Any]) -> dict[str, Any] | None:
    if str(proxy.get("network", "")).lower() not in {"ws", "websocket"}:
        return None
    raw = proxy.get("ws-opts")
    opts: dict[str, Any] = raw if isinstance(raw, dict) else {}
    transport: dict[str, Any] = {"type": "ws"}
    if opts.get("path"):
        transport["path"] = str(opts["path"])
    headers = opts.get("headers")
    if isinstance(headers, dict) and headers:
        transport["headers"] = {str(k): str(v) for k, v in headers.items()}
    return transport


def grpc_transport(proxy: dict[str, Any]) -> dict[str, Any] | None:
    if str(proxy.get("network", "")).lower() != "grpc":
        return None
    raw = proxy.get("grpc-opts")
    opts: dict[str, Any] = raw if isinstance(raw, dict) else {}
    transport: dict[str, Any] = {"type": "grpc"}
    service_name = opts.get("grpc-service-name") or opts.get("serviceName") or opts.get("service_name")
    if service_name:
        transport["service_name"] = str(service_name)
    return transport


def httpupgrade_transport(proxy: dict[str, Any]) -> dict[str, Any] | None:
    if str(proxy.get("network", "")).lower() not in {"httpupgrade", "http-upgrade"}:
        return None
    raw = proxy.get("httpupgrade-opts")
    opts: dict[str, Any] = raw if isinstance(raw, dict) else {}
    transport: dict[str, Any] = {"type": "httpupgrade"}
    if opts.get("path"):
        transport["path"] = str(opts["path"])
    host = opts.get("host")
    if isinstance(host, list):
        transport["host"] = [str(x) for x in host]
    elif host:
        transport["host"] = [str(host)]
    return transport


def add_supported_transport(proxy: dict[str, Any], outbound: dict[str, Any]) -> str | None:
    network = str(proxy.get("network", "")).lower()
    if not network or network in {"tcp", "raw"}:
        return None
    transport = websocket_transport(proxy) or grpc_transport(proxy) or httpupgrade_transport(proxy)
    if transport:
        outbound["transport"] = transport
        return None
    return f"unsupported transport {network}"


# --------------------------------------------------------------------------- #
# 协议转换
# --------------------------------------------------------------------------- #
def convert_anytls(proxy: dict[str, Any], tag: str) -> dict[str, Any]:
    reason = require_fields(proxy, ["server", "password"])
    if reason:
        raise ConversionError(reason)
    outbound = base_outbound(proxy, tag, "anytls")
    outbound["password"] = str(proxy["password"])
    outbound["tls"] = tls_config(proxy, default_enabled=True) or {"enabled": True}
    return outbound


def convert_trojan(proxy: dict[str, Any], tag: str) -> dict[str, Any]:
    reason = require_fields(proxy, ["server", "password"])
    if reason:
        raise ConversionError(reason)
    outbound = base_outbound(proxy, tag, "trojan")
    outbound["password"] = str(proxy["password"])
    tls = tls_config(proxy, default_enabled=True)
    if tls:
        outbound["tls"] = tls
    err = add_supported_transport(proxy, outbound)
    if err:
        raise ConversionError(err)
    return outbound


def convert_shadowsocks(proxy: dict[str, Any], tag: str) -> dict[str, Any]:
    method = proxy.get("cipher") or proxy.get("method")
    if method in (None, ""):
        raise ConversionError("missing cipher")
    reason = require_fields(proxy, ["server", "password"])
    if reason:
        raise ConversionError(reason)
    outbound = base_outbound(proxy, tag, "shadowsocks")
    outbound["method"] = str(method)
    outbound["password"] = str(proxy["password"])
    if proxy.get("udp") is not None and not parse_bool(proxy.get("udp")):
        outbound["network"] = "tcp"
    plugin = str(proxy.get("plugin") or "").lower()
    if plugin:
        raw = proxy.get("plugin-opts")
        opts: dict[str, Any] = raw if isinstance(raw, dict) else {}
        if plugin == "v2ray-plugin":
            mode = str(opts.get("mode") or "websocket").lower()
            if mode not in {"websocket", "quic"}:
                raise ConversionError(f"unsupported shadowsocks v2ray-plugin mode {mode}")
            plugin_opts = [f"mode={mode}"]
            if parse_bool(opts.get("tls")):
                plugin_opts.append("tls")
            for src, dst in (("host", "host"), ("path", "path")):
                if opts.get(src):
                    plugin_opts.append(f"{dst}={opts[src]}")
            outbound["plugin"] = "v2ray-plugin"
            outbound["plugin_opts"] = ";".join(plugin_opts)
            return outbound
        if plugin != "obfs":
            raise ConversionError(f"unsupported shadowsocks plugin {plugin}")
        mode = str(opts.get("mode") or "http").lower()
        host = opts.get("host")
        if mode not in {"http", "tls"}:
            raise ConversionError(f"unsupported shadowsocks obfs mode {mode}")
        plugin_opts = f"obfs={mode}"
        if host:
            plugin_opts += f";obfs-host={host}"
        outbound["plugin"] = "obfs-local"
        outbound["plugin_opts"] = plugin_opts
    return outbound


def convert_vmess(proxy: dict[str, Any], tag: str) -> dict[str, Any]:
    reason = require_fields(proxy, ["server", "uuid"])
    if reason:
        raise ConversionError(reason)
    outbound = base_outbound(proxy, tag, "vmess")
    outbound["uuid"] = str(proxy["uuid"])
    outbound["security"] = str(proxy.get("cipher") or "auto")
    outbound["alter_id"] = int(proxy.get("alterId") or proxy.get("alter-id") or 0)
    tls = tls_config(proxy)
    if tls:
        outbound["tls"] = tls
    err = add_supported_transport(proxy, outbound)
    if err:
        raise ConversionError(err)
    return outbound


def convert_vless(proxy: dict[str, Any], tag: str) -> dict[str, Any]:
    reason = require_fields(proxy, ["server", "uuid"])
    if reason:
        raise ConversionError(reason)
    outbound = base_outbound(proxy, tag, "vless")
    outbound["uuid"] = str(proxy["uuid"])
    if proxy.get("flow"):
        outbound["flow"] = str(proxy["flow"])
    tls = tls_config(proxy)
    if tls:
        outbound["tls"] = tls
    err = add_supported_transport(proxy, outbound)
    if err:
        raise ConversionError(err)
    return outbound


def convert_hysteria2(proxy: dict[str, Any], tag: str) -> dict[str, Any]:
    reason = require_fields(proxy, ["server", "password"])
    if reason:
        raise ConversionError(reason)
    outbound = base_outbound(proxy, tag, "hysteria2")
    outbound["password"] = str(proxy["password"])
    tls = tls_config(proxy, default_enabled=True)
    if tls:
        outbound["tls"] = tls
    return outbound


def convert_tuic(proxy: dict[str, Any], tag: str) -> dict[str, Any]:
    reason = require_fields(proxy, ["server", "uuid", "password"])
    if reason:
        raise ConversionError(reason)
    outbound = base_outbound(proxy, tag, "tuic")
    outbound["uuid"] = str(proxy["uuid"])
    outbound["password"] = str(proxy["password"])
    cc = proxy.get("congestion-controller") or proxy.get("congestion_control")
    if cc:
        outbound["congestion_control"] = str(cc)
    tls = tls_config(proxy, default_enabled=True)
    if tls:
        outbound["tls"] = tls
    return outbound


def convert_socks(proxy: dict[str, Any], tag: str) -> dict[str, Any]:
    reason = require_fields(proxy, ["server"])
    if reason:
        raise ConversionError(reason)
    outbound = base_outbound(proxy, tag, "socks")
    if proxy.get("username"):
        outbound["username"] = str(proxy["username"])
    if proxy.get("password"):
        outbound["password"] = str(proxy["password"])
    return outbound


def convert_http(proxy: dict[str, Any], tag: str) -> dict[str, Any]:
    reason = require_fields(proxy, ["server"])
    if reason:
        raise ConversionError(reason)
    outbound = base_outbound(proxy, tag, "http")
    if proxy.get("username"):
        outbound["username"] = str(proxy["username"])
    if proxy.get("password"):
        outbound["password"] = str(proxy["password"])
    tls = tls_config(proxy)
    if tls:
        outbound["tls"] = tls
    return outbound


_CONVERTERS = {
    "anytls": convert_anytls,
    "trojan": convert_trojan,
    "ss": convert_shadowsocks,
    "shadowsocks": convert_shadowsocks,
    "vmess": convert_vmess,
    "vless": convert_vless,
    "hysteria2": convert_hysteria2,
    "hy2": convert_hysteria2,
    "tuic": convert_tuic,
    "socks": convert_socks,
    "socks5": convert_socks,
    "http": convert_http,
}


def convert_proxy(proxy: dict[str, Any], used_tags: set[str]) -> tuple[dict[str, Any] | None, str | None]:
    if not isinstance(proxy, dict):
        return None, "proxy is not a mapping"
    name = str(proxy.get("name") or "").strip()
    if not name:
        return None, "missing name"
    tag = make_safe_tag(name, used_tags)
    proxy_type = str(proxy.get("type") or "").lower().strip()
    converter = _CONVERTERS.get(proxy_type)
    if converter is None:
        used_tags.discard(tag)
        return None, f"unsupported type {proxy_type or 'unknown'}"
    try:
        return converter(proxy, tag), None
    except ConversionError as exc:
        used_tags.discard(tag)
        return None, str(exc)


# --------------------------------------------------------------------------- #
# 配置各部分
# --------------------------------------------------------------------------- #
def build_inbounds(c: CustomConfig) -> list[dict[str, Any]]:
    tun_inbound: dict[str, Any] = {
        "type": "tun", "tag": "tun-in", "interface_name": "singbox",
        "address": ["172.19.0.1/30"], "mtu": 1400, "auto_route": True,
        "strict_route": True, "route_exclude_address": list(c.route_exclude_ip_cidrs),
        "stack": "gvisor",
    }
    if c.tun_exclude_uids:
        tun_inbound["exclude_uid"] = c.tun_exclude_uids
    proxy_listen = "0.0.0.0" if getattr(c, "lan_proxy", False) else "127.0.0.1"
    return [
        tun_inbound,
        {"type": "mixed", "tag": "mixed-in", "listen": proxy_listen, "listen_port": 7890},
    ]


def build_dns(c: CustomConfig) -> dict[str, Any]:
    rules: list[dict[str, Any]] = [
        {"domain": c.local_bypass_domains, "action": "route", "server": BOOTSTRAP_DNS_TAG},
    ]
    if c.direct_domain_suffixes:
        rules.append({"domain_suffix": c.direct_domain_suffixes, "action": "route", "server": BOOTSTRAP_DNS_TAG})
    rules.extend([
        {"domain_suffix": c.ai_domain_suffixes, "action": "route", "server": REMOTE_DNS_TAG},
        {"domain_suffix": c.streaming_domain_suffixes, "action": "route", "server": REMOTE_DNS_TAG},
        {"rule_set": GEOSITE_CN_RULE_SET_TAG, "action": "route", "server": BOOTSTRAP_DNS_TAG},
        {"rule_set": GEOIP_CN_RULE_SET_TAG, "action": "route", "server": BOOTSTRAP_DNS_TAG},
    ])
    if (c.bootstrap_dns_server or "").lower() == BOOTSTRAP_DNS_DHCP:
        bootstrap_server: dict[str, Any] = {"type": "dhcp", "tag": BOOTSTRAP_DNS_TAG, "detour": "DIRECT"}
    else:
        bootstrap_server = {
            "type": "udp", "tag": BOOTSTRAP_DNS_TAG, "server": c.bootstrap_dns_server,
            "server_port": c.bootstrap_dns_port, "detour": "DIRECT",
        }
    return {
        "servers": [
            bootstrap_server,
            {"type": "udp", "tag": "dns-dnspod", "server": "119.29.29.29", "server_port": 53, "detour": "DIRECT"},
            {"type": "https", "tag": REMOTE_DNS_TAG, "server": "1.1.1.1", "server_port": 443,
             "path": "/dns-query", "tls": {"server_name": "cloudflare-dns.com"}, "detour": "Proxy"},
        ],
        "rules": rules,
        "final": REMOTE_DNS_TAG,
        "strategy": "prefer_ipv4",
        "cache_capacity": 4096,
    }


def clash_api_controller(lan_access: bool) -> str:
    return "0.0.0.0:9090" if lan_access else "127.0.0.1:9090"


def build_experimental(lan_access: bool = False) -> dict[str, Any]:
    clash_api: dict[str, Any] = {
        "external_controller": clash_api_controller(lan_access),
        "external_ui": str(paths.UI_DIR),
        "default_mode": "rule",
    }
    if lan_access:
        clash_api["access_control_allow_private_network"] = True
    return {"clash_api": clash_api}


def build_rule_sets() -> list[dict[str, Any]]:
    return [
        {"type": "local", "tag": GEOSITE_CN_RULE_SET_TAG, "format": "binary", "path": str(paths.GEOSITE_CN)},
        {"type": "local", "tag": GEOIP_CN_RULE_SET_TAG, "format": "binary", "path": str(paths.GEOIP_CN)},
    ]


def build_route(default_outbound: str, has_sg_auto: bool, has_hk_auto: bool, c: CustomConfig) -> dict[str, Any]:
    if default_outbound in {"SG-Auto", "SG-Fallback"} and not has_sg_auto:
        default_outbound = "Proxy"
    if default_outbound in {"HK-Auto", "HK-Fallback"} and not has_hk_auto:
        default_outbound = "Proxy"
    rules: list[dict[str, Any]] = [
        {"process_name": c.bypass_process_names, "action": "route", "outbound": "DIRECT"},
        {"domain": c.local_bypass_domains, "action": "route", "outbound": "DIRECT"},
        {"ip_cidr": c.route_exclude_ip_cidrs, "action": "route", "outbound": "DIRECT"},
        {"action": "sniff"},
        {"protocol": "dns", "action": "hijack-dns"},
        {"ip_is_private": True, "action": "route", "outbound": "DIRECT"},
    ]
    if c.direct_domain_suffixes:
        rules.append({"domain_suffix": c.direct_domain_suffixes, "action": "route", "outbound": DIRECT_GROUP_TAG})
    rules.extend([
        {"domain_suffix": c.ai_domain_suffixes, "action": "route", "outbound": "AI"},
        {"domain_suffix": c.streaming_domain_suffixes, "action": "route", "outbound": "Streaming"},
        {"rule_set": [GEOSITE_CN_RULE_SET_TAG, GEOIP_CN_RULE_SET_TAG], "action": "route", "outbound": "DIRECT"},
    ])
    return {
        "auto_detect_interface": True,
        "default_domain_resolver": dict(BOOTSTRAP_DOMAIN_RESOLVER),
        "rules": rules,
        "rule_set": build_rule_sets(),
        "final": default_outbound,
    }


def build_outbounds(converted_nodes: list[dict[str, Any]], c: CustomConfig) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    node_tags = [n["tag"] for n in converted_nodes]
    selectable_tags = [t for t in node_tags if not is_informational_node(t)]

    sg_tags = (
        [t for t in selectable_tags if is_preferred_node(t, c.prefer_keywords) and not is_excluded_sg_node(t)]
        if c.generate_sg_groups else []
    )
    has_sg_auto = bool(sg_tags)
    hk_tags = (
        [t for t in selectable_tags if is_preferred_node(t, c.hk_prefer_keywords) and not is_excluded_hk_node(t)]
        if c.generate_hk_groups else []
    )
    has_hk_auto = bool(hk_tags)

    outbounds: list[dict[str, Any]] = list(converted_nodes)
    sg_group_outbounds: list[dict[str, Any]] = []
    if has_sg_auto:
        sg_group_outbounds.append({
            "type": "urltest", "tag": "SG-Auto", "outbounds": sg_tags,
            "url": "https://www.gstatic.com/generate_204", "interval": "5m", "tolerance": 50,
        })
        sg_group_outbounds.append({"type": "selector", "tag": "SG-Fallback", "outbounds": sg_tags, "default": sg_tags[0]})

    hk_group_outbounds: list[dict[str, Any]] = []
    if has_hk_auto:
        hk_group_outbounds.append({
            "type": "urltest", "tag": "HK-Auto", "outbounds": hk_tags,
            "url": "https://www.gstatic.com/generate_204", "interval": "5m", "tolerance": 50,
        })
        hk_group_outbounds.append({"type": "selector", "tag": "HK-Fallback", "outbounds": hk_tags, "default": hk_tags[0]})

    region_groups: list[str] = []
    if has_sg_auto:
        region_groups.extend(["SG-Auto", "SG-Fallback"])
    if has_hk_auto:
        region_groups.extend(["HK-Auto", "HK-Fallback"])

    auto_outbound = {
        "type": "urltest", "tag": "Auto", "outbounds": selectable_tags,
        "url": "https://www.gstatic.com/generate_204", "interval": "5m", "tolerance": 50,
    }

    ai_outbounds = ["Proxy", *region_groups, "Auto", "DIRECT"]
    outbounds.append({"type": "selector", "tag": "AI", "outbounds": ai_outbounds, "default": "Proxy"})
    streaming_outbounds = ["Proxy", *region_groups, "Auto", "DIRECT"]
    outbounds.append({"type": "selector", "tag": "Streaming", "outbounds": streaming_outbounds, "default": "Proxy"})

    if has_sg_auto:
        proxy_default = "SG-Auto"
    elif has_hk_auto:
        proxy_default = "HK-Auto"
    else:
        proxy_default = "Auto"
    proxy_outbounds: list[str] = [*region_groups, "Auto", *selectable_tags, "DIRECT"]
    outbounds.append({"type": "selector", "tag": "Proxy", "outbounds": proxy_outbounds, "default": proxy_default})

    outbounds.extend([*sg_group_outbounds, *hk_group_outbounds, auto_outbound])
    outbounds.extend([
        {"type": "direct", "tag": "DIRECT", "domain_resolver": dict(BOOTSTRAP_DOMAIN_RESOLVER)},
        {"type": "block", "tag": "BLOCK"},
        {"type": "selector", "tag": "Fallback", "outbounds": ["Proxy", "Auto", "DIRECT"], "default": "Proxy"},
    ])

    has_direct_group = bool(c.direct_domain_suffixes)
    if has_direct_group:
        outbounds.append({
            "type": "selector", "tag": DIRECT_GROUP_TAG,
            "outbounds": ["DIRECT", "Proxy", "Auto"], "default": "DIRECT",
        })

    info = {
        "has_sg_auto": has_sg_auto, "sg_count": len(sg_tags),
        "has_hk_auto": has_hk_auto, "hk_count": len(hk_tags),
        "auto_count": len(selectable_tags), "proxy_default": proxy_default,
        "has_direct_group": has_direct_group, "direct_count": len(c.direct_domain_suffixes),
    }
    return outbounds, info


def build_singbox_config(converted_nodes: list[dict[str, Any]], c: CustomConfig, lan_access: bool = False) -> tuple[dict[str, Any], dict[str, Any]]:
    outbounds, info = build_outbounds(converted_nodes, c)
    default_outbound = c.default_outbound
    if default_outbound in {"SG-Auto", "SG-Fallback"} and not info["has_sg_auto"]:
        default_outbound = "Proxy"
    if default_outbound in {"HK-Auto", "HK-Fallback"} and not info["has_hk_auto"]:
        default_outbound = "Proxy"
    config = {
        "log": {"level": "warning"},
        "dns": build_dns(c),
        "inbounds": build_inbounds(c),
        "outbounds": outbounds,
        "route": build_route(default_outbound, info["has_sg_auto"], info["has_hk_auto"], c),
        "experimental": build_experimental(lan_access),
    }
    return config, info


# --------------------------------------------------------------------------- #
# 校验
# --------------------------------------------------------------------------- #
def validate_config_basic(config: dict[str, Any]) -> None:
    if not isinstance(config, dict):
        raise ConversionError("config must be a dict")
    for key in ("inbounds", "outbounds"):
        if not isinstance(config.get(key), list) or not config[key]:
            raise ConversionError(f"{key} must be a non-empty list")
    if not isinstance(config.get("route"), dict):
        raise ConversionError("route must exist")
    if not isinstance(config.get("dns"), dict):
        raise ConversionError("dns must exist")
    clash_api = (config.get("experimental") or {}).get("clash_api")
    if not isinstance(clash_api, dict):
        raise ConversionError("experimental.clash_api must exist")
    if not clash_api.get("external_controller") or not clash_api.get("external_ui"):
        raise ConversionError("clash_api external_controller and external_ui must exist")

    tags = [o.get("tag") for o in config["outbounds"]]
    for o in config["outbounds"]:
        if not isinstance(o, dict) or not o.get("type") or not o.get("tag"):
            raise ConversionError("each outbound must have type and tag")
    tag_set = set(tags)
    if len(tags) != len(tag_set):
        raise ConversionError("outbound tags must be unique")
    for o in config["outbounds"]:
        if o["type"] in {"selector", "urltest"}:
            refs = o.get("outbounds")
            if not isinstance(refs, list) or not refs:
                raise ConversionError(f"{o['tag']} outbounds must be non-empty")
            missing = [r for r in refs if r not in tag_set]
            if missing:
                raise ConversionError(f"{o['tag']} references missing outbounds: {missing}")
    if config["route"].get("final") not in tag_set:
        raise ConversionError(f"route final references missing outbound: {config['route'].get('final')}")


# --------------------------------------------------------------------------- #
# 顶层入口
# --------------------------------------------------------------------------- #
def clash_to_singbox(yaml_text: str, c: CustomConfig, *, strict: bool = False) -> tuple[dict[str, Any], dict[str, Any]]:
    """Clash YAML 文本 → (sing-box 配置, 概要信息)。"""
    data = yamlmini.load(yaml_text)
    if not isinstance(data, dict):
        raise ConversionError("订阅 YAML 根必须是映射。")
    proxies = data.get("proxies")
    if not isinstance(proxies, list) or not proxies:
        raise ConversionError("订阅缺少 proxies 列表或为空。")

    used_tags = set(RESERVED_TAGS)
    converted: list[dict[str, Any]] = []
    skipped: Counter[str] = Counter()
    for proxy in proxies:
        outbound, reason = convert_proxy(proxy, used_tags)
        if reason:
            skipped[reason] += 1
            if strict:
                raise ConversionError(f"strict: 跳过节点: {reason}")
            continue
        assert outbound is not None
        converted.append(outbound)
    if not converted:
        raise ConversionError("没有任何节点转换成功。")

    config, info = build_singbox_config(converted, c, lan_access=c.lan_panel)
    validate_config_basic(config)
    info.update({"total": len(proxies), "converted": len(converted), "skipped": dict(skipped)})
    return config, info


def to_singbox(raw: str | bytes, source_type: str, customize: bool, cfg: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """三来源统一入口。base64 在阶段7接入；此处先支持 clash 与 singbox 直链。"""
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", "ignore")
    c = to_custom_config(cfg)
    if source_type == "clash":
        return clash_to_singbox(raw, c)
    if source_type == "singbox":
        return _singbox_direct(raw, c, customize)
    if source_type == "base64":
        return _base64(raw, c, cfg)
    raise ConversionError(f"未知来源类型: {source_type}")


def _base64(raw: str, c: CustomConfig, cfg: dict) -> tuple[dict[str, Any], dict[str, Any]]:
    """base64 默认走云端 subconverter→clash→本地转换器；失败按配置降级本地解析。"""
    from . import b64

    backend = str(cfg.get("subconverter_backend") or "")
    proxy = str(cfg.get("download_proxy") or "")
    if backend:
        try:
            from .. import shell
            shell.info(f"经 subconverter 解析 base64（{backend}）…")
            clash_yaml = b64.to_clash_via_subconverter(raw, backend, proxy=proxy)
            return clash_to_singbox(clash_yaml, c)
        except (RuntimeError, ConversionError) as exc:
            if not cfg.get("base64_local_fallback"):
                raise ConversionError(
                    f"subconverter 解析失败：{exc}。可换后端，或开启应急本地解析 base64_local_fallback。"
                ) from exc
            from .. import shell
            shell.warn(f"subconverter 失败，改用应急本地解析：{exc}")
    elif not cfg.get("base64_local_fallback"):
        raise ConversionError("未配置 subconverter 后端，且未开启应急本地解析。")

    # 应急本地解析（best-effort）
    proxies = b64.local_parse_to_clash(raw)
    if not proxies:
        raise ConversionError("本地解析未得到任何节点。")
    used_tags = set(RESERVED_TAGS)
    converted: list[dict[str, Any]] = []
    for proxy_dict in proxies:
        outbound, _ = convert_proxy(proxy_dict, used_tags)
        if outbound is not None:
            converted.append(outbound)
    if not converted:
        raise ConversionError("本地解析的节点均不受支持。")
    config, info = build_singbox_config(converted, c, lan_access=c.lan_panel)
    validate_config_basic(config)
    info.update({"total": len(proxies), "converted": len(converted), "mode": "base64-local"})
    return config, info


def _singbox_direct(raw: str, c: CustomConfig, customize: bool) -> tuple[dict[str, Any], dict[str, Any]]:
    import json
    try:
        config = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ConversionError(f"sing-box 直链不是合法 JSON: {exc}") from exc
    if not isinstance(config, dict):
        raise ConversionError("sing-box 配置根必须是对象。")
    if not customize:
        # 原样使用；补 clash_api/UI 便于面板访问
        config.setdefault("experimental", {}).setdefault("clash_api", build_experimental(c.lan_panel)["clash_api"])
        return config, {"mode": "passthrough"}
    # 注入定制：抽取真实节点 outbound 作为 converted_nodes
    nodes = [o for o in config.get("outbounds", []) if isinstance(o, dict)
             and o.get("type") not in {"selector", "urltest", "direct", "block", "dns"} and o.get("tag")]
    if not nodes:
        raise ConversionError("sing-box 直链中未找到可用节点 outbound。")
    new_config, info = build_singbox_config(nodes, c, lan_access=c.lan_panel)
    validate_config_basic(new_config)
    info["mode"] = "customized"
    return new_config, info
