"""命名订阅管理：增 / 删 / 改名 / 切换 active / 刷新 / 列表。

每个订阅存于 state/subscriptions/<name>/：meta.json + raw.* + config.json。
active 指针（state/active）决定哪份部署生效；切换会同步 state/config.json 并重启服务。
"""

from __future__ import annotations

import json
import shutil
from dataclasses import asdict, dataclass
from datetime import datetime, timezone

from .. import customize, paths, service, shell
from . import convert, detect, fetch

_EXT = {"clash": "yaml", "singbox": "json", "base64": "txt"}


@dataclass
class Subscription:
    name: str
    url: str
    source_type: str
    customize: bool = True
    converter: str = "local"
    created_at: str = ""
    updated_at: str = ""
    last_node_count: int = 0


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _slug(name: str) -> str:
    name = name.strip().replace("/", "-").replace("\\", "-").replace("..", "-")
    name = "-".join(name.split())  # 折叠空白
    return name.strip(". ") or "sub"


def _dir(name: str):
    return paths.subscription_dir(name)


def _meta_file(name: str):
    return _dir(name) / "meta.json"


def _config_file(name: str):
    return _dir(name) / "config.json"


# --------------------------------------------------------------------------- #
# 读取
# --------------------------------------------------------------------------- #
def list_all() -> "list[Subscription]":
    subs: list[Subscription] = []
    if not paths.SUBSCRIPTIONS_DIR.exists():
        return subs
    for d in sorted(paths.SUBSCRIPTIONS_DIR.iterdir()):
        meta = d / "meta.json"
        if meta.exists():
            try:
                subs.append(Subscription(**json.loads(meta.read_text("utf-8"))))
            except (json.JSONDecodeError, TypeError, OSError):
                continue
    return subs


def get(name: str) -> "Subscription | None":
    f = _meta_file(name)
    if not f.exists():
        return None
    try:
        return Subscription(**json.loads(f.read_text("utf-8")))
    except (json.JSONDecodeError, TypeError, OSError):
        return None


def get_active() -> "Subscription | None":
    if not paths.ACTIVE_FILE.exists():
        return None
    return get(paths.ACTIVE_FILE.read_text("utf-8").strip())


# --------------------------------------------------------------------------- #
# 增 / 改
# --------------------------------------------------------------------------- #
def add(name: str, url: str, source_type: str, *, customize_flag: bool = True, set_active: bool = True) -> Subscription:
    name = _slug(name)
    if _meta_file(name).exists():
        raise RuntimeError(f"订阅「{name}」已存在，请改名或先删除。")
    sub = Subscription(
        name=name, url=url, source_type=source_type, customize=customize_flag,
        created_at=_now(), updated_at=_now(),
    )
    _build(sub)
    if set_active:
        _apply_active(name)
    return sub


def refresh(name: str) -> Subscription:
    """联网重新拉取订阅原文并重转（用于「刷新订阅」/ 定时更新）。"""
    sub = get(name)
    if sub is None:
        raise RuntimeError(f"订阅不存在: {name}")
    sub.updated_at = _now()
    _build(sub)
    if get_active() and get_active().name == name:  # type: ignore[union-attr]
        _apply_active(name)
    return sub


def rebuild(name: str) -> Subscription:
    """基于本地已存订阅原文重新转换（不联网），用于应用定制层等本地改动。

    订阅链接一般只在「刷新」时才重拉；改定制层只需把本地原文按新设置重转即可。
    本地无原文（异常情况）时回退为联网刷新。
    """
    sub = get(name)
    if sub is None:
        raise RuntimeError(f"订阅不存在: {name}")
    raw_file = _raw_file(sub)
    if not raw_file.exists():
        shell.warn("本地缺少订阅原文，改为联网刷新。")
        return refresh(name)
    sub.updated_at = _now()
    shell.info(f"用本地原文重新生成「{sub.name}」（不重新拉取）…")
    _convert_and_write(sub, raw_file.read_bytes(), customize.load())
    if get_active() and get_active().name == name:  # type: ignore[union-attr]
        _apply_active(name)
    return sub


def _raw_file(sub: Subscription):
    return _dir(sub.name) / f"raw.{_EXT.get(sub.source_type, 'txt')}"


def _build(sub: Subscription) -> None:
    """拉取 → 写 raw → 转换写盘。"""
    cfg = customize.load()
    proxy = str(cfg.get("download_proxy") or "")
    shell.info(f"拉取订阅「{sub.name}」…")
    raw = fetch.fetch(sub.url, source_type=sub.source_type, proxy=proxy)

    mismatch = detect.warn_if_mismatch(sub.source_type, raw)
    if mismatch:
        shell.warn(mismatch)

    _dir(sub.name).mkdir(parents=True, exist_ok=True)
    _raw_file(sub).write_bytes(raw)
    _convert_and_write(sub, raw, cfg)


def _convert_and_write(sub: Subscription, raw: bytes, cfg: dict) -> None:
    """把订阅原文按当前 customize 转换为 sing-box 配置并写 config/meta。"""
    shell.info("转换为 sing-box 配置…")
    config, info = convert.to_singbox(raw, sub.source_type, sub.customize, cfg)
    sub.last_node_count = int(info.get("converted", info.get("auto_count", 0)) or 0)

    _config_file(sub.name).write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", "utf-8")
    _meta_file(sub.name).write_text(json.dumps(asdict(sub), ensure_ascii=False, indent=2) + "\n", "utf-8")
    skipped = info.get("skipped") or {}
    extra = f"，跳过 {sum(skipped.values())}" if skipped else ""
    shell.ok(f"订阅「{sub.name}」就绪：{sub.last_node_count} 节点{extra}")


# --------------------------------------------------------------------------- #
# 切换 / 删除 / 改名
# --------------------------------------------------------------------------- #
def switch(name: str) -> None:
    if not _meta_file(name).exists():
        raise RuntimeError(f"订阅不存在: {name}")
    _apply_active(name)
    shell.ok(f"已切换生效订阅: {name}")


def _apply_active(name: str) -> None:
    paths.ensure_state_dirs()
    shutil.copyfile(_config_file(name), paths.CONFIG_FILE)
    paths.ACTIVE_FILE.write_text(name + "\n", "utf-8")
    if service.is_installed():
        try:
            service.sync_and_restart()
        except (RuntimeError, shell.CommandError) as exc:
            shell.warn(f"配置已切换，但同步到服务失败：{exc}")


def remove(name: str) -> None:
    d = _dir(name)
    if not d.exists():
        raise RuntimeError(f"订阅不存在: {name}")
    was_active = get_active() and get_active().name == name  # type: ignore[union-attr]
    shutil.rmtree(d, ignore_errors=True)
    if was_active:
        paths.ACTIVE_FILE.unlink(missing_ok=True)
        shell.warn("已删除当前生效订阅；请切换到其它订阅或重新添加。")
    shell.ok(f"已删除订阅: {name}")


def rename(old: str, new: str) -> None:
    new = _slug(new)
    if not _meta_file(old).exists():
        raise RuntimeError(f"订阅不存在: {old}")
    if _meta_file(new).exists():
        raise RuntimeError(f"目标名已存在: {new}")
    _dir(old).rename(_dir(new))
    sub = get(new)
    if sub:
        sub.name = new
        sub.updated_at = _now()
        _meta_file(new).write_text(json.dumps(asdict(sub), ensure_ascii=False, indent=2) + "\n", "utf-8")
    if get_active() and get_active().name == old:  # type: ignore[union-attr]
        paths.ACTIVE_FILE.write_text(new + "\n", "utf-8")
    shell.ok(f"已改名: {old} → {new}")
