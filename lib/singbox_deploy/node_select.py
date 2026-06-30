"""交互式切换 / 固定首选节点。

移植自参考仓库 select_singbox_node.py：把选中项设为目标 selector（默认 Proxy）的
第一个成员并对齐 default，使重启后稳定停在该节点；服务在跑时还经 Clash API 实时切换，
并并发实测延迟。重启改用 service.sync_and_restart。仅依赖标准库（urllib）。
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from . import keys, menu, paths, service, shell
from .subscription import manager

GROUP_TYPES = {"selector", "urltest"}
NON_NODE_TYPES = {"direct", "block", "dns"}
INFO_KEYWORDS = ("Traffic:", "Expire:", "剩余流量", "过期时间", "剩余", "套餐", "官网", "订阅", "重置")
REGIONS = [
    ("hk", "🇭🇰 香港", ("香港", "hong kong", "hongkong")),
    ("tw", "🇹🇼 台湾", ("台湾", "臺灣", "taiwan")),
    ("jp", "🇯🇵 日本", ("日本", "japan", "东京", "大阪")),
    ("kr", "🇰🇷 韩国", ("韩国", "韓國", "korea", "首尔")),
    ("sg", "🇸🇬 新加坡", ("新加坡", "singapore", "狮城", "獅城")),
    ("us", "🇺🇸 美国", ("美国", "united states", "america", "硅谷", "洛杉矶", "圣何塞")),
]
OTHER_KEY, OTHER_LABEL = "other", "🌐 其他地区"
DELAY_URL = "https://www.gstatic.com/generate_204"
DELAY_TIMEOUT_MS = 5000


def pick_group(config: dict, forced: str = "") -> dict:
    selectors = [o for o in config.get("outbounds", []) if o.get("type") == "selector"]
    if not selectors:
        raise RuntimeError("配置里没有 selector 分组，无法切换节点。")
    if forced:
        for o in selectors:
            if o.get("tag") == forced:
                return o
        raise RuntimeError(f"指定分组 '{forced}' 不存在。")
    for o in selectors:
        if o.get("tag") == "Proxy":
            return o
    return max(selectors, key=lambda o: len(o.get("outbounds", [])))


def _classify(tag: str) -> str:
    low = tag.lower()
    for key, _label, kws in REGIONS:
        if any(kw in tag or kw in low for kw in kws):
            return key
    return OTHER_KEY


def _is_info(tag: str) -> bool:
    return any(kw in tag for kw in INFO_KEYWORDS)


def collect_members(config: dict, group: dict) -> "tuple[dict[str, list[str]], list[str]]":
    type_by_tag = {o.get("tag"): o.get("type") for o in config.get("outbounds", [])}
    buckets: dict[str, list[str]] = {}
    groups: list[str] = []
    for tag in group.get("outbounds", []):
        otype = type_by_tag.get(tag)
        if otype in GROUP_TYPES:
            groups.append(tag)
        elif otype in NON_NODE_TYPES or _is_info(tag):
            continue
        else:
            buckets.setdefault(_classify(tag), []).append(tag)
    return buckets, groups


# --------------------------------------------------------------------------- #
# Clash API
# --------------------------------------------------------------------------- #
def _clash_base(config: dict) -> "tuple[str, dict] | None":
    api = config.get("experimental", {}).get("clash_api") or {}
    controller = api.get("external_controller")
    if not controller:
        return None
    host, _, port = controller.partition(":")
    if host in ("", "0.0.0.0", "::"):
        host = "127.0.0.1"
    headers = {"Content-Type": "application/json"}
    if api.get("secret"):
        headers["Authorization"] = f"Bearer {api['secret']}"
    return f"http://{host}:{port or '9090'}", headers


def _api_reachable(base: str, headers: dict) -> bool:
    try:
        with urllib.request.urlopen(urllib.request.Request(f"{base}/version", headers=headers), timeout=2):
            return True
    except (urllib.error.URLError, OSError):
        return False


def _api_switch(base: str, headers: dict, group: str, node: str) -> bool:
    body = json.dumps({"name": node}).encode()
    req = urllib.request.Request(f"{base}/proxies/{urllib.parse.quote(group)}", data=body, headers=headers, method="PUT")
    try:
        with urllib.request.urlopen(req, timeout=4):
            return True
    except (urllib.error.URLError, OSError) as exc:
        shell.warn(f"Clash API 实时切换失败：{exc}")
        return False


def _api_delay(base: str, headers: dict, name: str) -> "int | None":
    q = urllib.parse.urlencode({"url": DELAY_URL, "timeout": DELAY_TIMEOUT_MS})
    url = f"{base}/proxies/{urllib.parse.quote(name)}/delay?{q}"
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=DELAY_TIMEOUT_MS / 1000 + 2) as resp:
            return json.load(resp).get("delay")
    except (urllib.error.URLError, OSError, json.JSONDecodeError, ValueError):
        return None


def _measure(api: "tuple[str, dict]", names: "list[str]") -> "dict[str, int | None]":
    base, headers = api
    if not names:
        return {}
    total = len(names)
    tty = keys.interactive_tty()
    if not tty:
        shell.info(f"测速中（{total} 个节点）…")
    results: dict[str, "int | None"] = {}
    done = 0
    with ThreadPoolExecutor(max_workers=min(16, total)) as ex:
        futures = {ex.submit(_api_delay, base, headers, n): n for n in names}
        for fut in as_completed(futures):
            name = futures[fut]
            results[name] = fut.result()
            done += 1
            if tty:
                sys.stdout.write(f"\r\033[K  测速中… {done}/{total}")
                sys.stdout.flush()
    if tty:
        sys.stdout.write("\r\033[K")
        sys.stdout.flush()
    ok = sum(1 for v in results.values() if v is not None)
    shell.ok(f"测速完成：{ok}/{total} 可用")
    return results


def _fmt_delay(ms: "int | None") -> str:
    if ms is None:
        return "超时"
    return f"{ms}ms"


# --------------------------------------------------------------------------- #
# 持久化
# --------------------------------------------------------------------------- #
def _persist_first(config: dict, group_tag: str, node: str, paths_to_write: "list[Path]") -> None:
    for o in config.get("outbounds", []):
        if o.get("type") == "selector" and o.get("tag") == group_tag:
            members = [t for t in o.get("outbounds", []) if t != node]
            o["outbounds"] = [node] + members
            o["default"] = node
            break
    payload = json.dumps(config, ensure_ascii=False, indent=2) + "\n"
    for p in paths_to_write:
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_text(payload, "utf-8")
        tmp.replace(p)


# --------------------------------------------------------------------------- #
# 主流程
# --------------------------------------------------------------------------- #
def select(config_path: str | None = None, group: str = "") -> None:
    path = Path(config_path) if config_path else paths.CONFIG_FILE
    if not path.is_file():
        raise RuntimeError(f"找不到配置文件：{path}")
    config = json.loads(path.read_text("utf-8"))
    target = pick_group(config, group)
    group_tag = target["tag"]
    buckets, groups = collect_members(config, target)
    if not buckets and not groups:
        raise RuntimeError(f"分组 '{group_tag}' 下没有可选项。")

    # 节点切换走 Clash API 热切换，无需预重启同步；直接连 API 实时测速/切换
    api = _clash_base(config)
    api_ok = bool(api and _api_reachable(*api))
    shell.info("已连上 Clash API，列表将实时测速。" if api_ok else "Clash API 不可达，跳过测速。")

    # 第一步：地区/分组
    first_menu: list[tuple[str, list[str]]] = []
    for key, label, _kw in REGIONS:
        if buckets.get(key):
            first_menu.append((label, buckets[key]))
    if buckets.get(OTHER_KEY):
        first_menu.append((OTHER_LABEL, buckets[OTHER_KEY]))
    if groups:
        first_menu.append(("🧭 分组（自动测速 / 故障转移）", groups))

    idx = menu.select("选择地区 / 分组", [f"{lbl}（{len(items)}）" for lbl, items in first_menu])
    label, items = first_menu[idx]

    # 第二步：具体节点（带测速）
    delays = _measure(api, items) if api_ok else {}  # type: ignore[arg-type]
    labels = [f"{tag}   {_fmt_delay(delays.get(tag))}" if api_ok else tag for tag in items]
    nidx = menu.select(label, labels)
    selected = items[nidx]

    # 应用：写 state/config.json + 当前 active 订阅的 config.json（双写以跨重启持久）
    targets = [path]
    active = manager.get_active()
    if active:
        sub_cfg = paths.subscription_dir(active.name) / "config.json"
        if sub_cfg.exists() and sub_cfg != path:
            targets.append(sub_cfg)
    _persist_first(config, group_tag, selected, targets)
    shell.ok(f"已固定 {group_tag} 首选 = {selected}")

    if api_ok and _api_switch(api[0], api[1], group_tag, selected):  # type: ignore[index]
        shell.ok(f"已通过 Clash API 实时切换 {group_tag} → {selected}")

    if service.is_installed() and menu.confirm("重启服务以确保生效？", default=False):
        service.sync_and_restart()


def run(argv: list[str] | None = None) -> int:
    try:
        select()
    except (RuntimeError, menu.Cancelled) as exc:
        if isinstance(exc, RuntimeError):
            shell.error(str(exc))
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
