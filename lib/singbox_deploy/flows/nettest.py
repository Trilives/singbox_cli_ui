"""网络测试：在当前网络条件下测主流流媒体 / 站点 / AI 服务的延迟，
并探测 OpenAI / Claude 等的出口 IP（经本地 sing-box 代理）。

- 优先经本地 mixed 入站 127.0.0.1:7890 走代理（即“走代理后的真实体验”）；
  代理端口未监听时回退直连，并在结果里标注。
- 延迟取 curl 的 TTFB（time_starttransfer），单位 ms；超时/失败显示“超时”。
- 出口 IP 借 Cloudflare 边缘的 /cdn-cgi/trace：返回的 ip= 即本次该服务方向的出口 IP，
  loc= 为出口国家/地区——OpenAI(chat.openai.com)、Claude(claude.ai) 均由 Cloudflare 兜底，
  故可按各自分流路径分别探测，结果反映实际落地 IP。
"""

from __future__ import annotations

import socket
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import NamedTuple

from .. import keys, shell

PROXY_HOST = "127.0.0.1"
PROXY_PORT = 7890
PROXY_URL = f"http://{PROXY_HOST}:{PROXY_PORT}"
_UA = "Mozilla/5.0 (X11; Linux x86_64) sing-box-nettest"
_MAX_TIME = 10  # 单目标超时（秒）

# (类别, 名称, 测延迟用的 URL)
_LATENCY_TARGETS: list[tuple[str, str, str]] = [
    ("流媒体", "Netflix", "https://www.netflix.com/title/80018499"),
    ("流媒体", "YouTube", "https://www.youtube.com/generate_204"),
    ("流媒体", "Disney+", "https://www.disneyplus.com/"),
    ("流媒体", "TikTok", "https://www.tiktok.com/"),
    ("流媒体", "Spotify", "https://www.spotify.com/"),
    ("站点", "Google", "https://www.google.com/generate_204"),
    ("站点", "GitHub", "https://github.com/"),
    ("站点", "Cloudflare", "https://www.cloudflare.com/cdn-cgi/trace"),
    ("站点", "Wikipedia", "https://en.wikipedia.org/"),
    ("AI", "OpenAI", "https://chat.openai.com/cdn-cgi/trace"),
    ("AI", "Claude", "https://claude.ai/cdn-cgi/trace"),
    ("AI", "Gemini", "https://gemini.google.com/"),
]

# 出口 IP 探测目标：cdn-cgi/trace 会回显本次出口 IP / 落地国家
_TRACE_TARGETS: list[tuple[str, str]] = [
    ("OpenAI", "https://chat.openai.com/cdn-cgi/trace"),
    ("Claude", "https://claude.ai/cdn-cgi/trace"),
    ("Cloudflare", "https://www.cloudflare.com/cdn-cgi/trace"),
]


class _Lat(NamedTuple):
    ms: int | None
    code: str


def _proxy_up() -> bool:
    """本地代理端口是否在监听（决定经代理还是直连）。"""
    try:
        with socket.create_connection((PROXY_HOST, PROXY_PORT), timeout=1):
            return True
    except OSError:
        return False


def _curl(url: str, via_proxy: bool, fmt: str, *, body: bool) -> tuple[int, str]:
    """跑一次 curl，返回 (returncode, stdout)。body=False 时丢弃响应体。"""
    import os

    args = ["curl", "-sS", "-A", _UA, "--max-time", str(_MAX_TIME), "-w", fmt]
    args += ["--proxy", PROXY_URL] if via_proxy else ["--noproxy", "*"]
    if not body:
        args += ["-o", os.devnull]
    args.append(url)
    res = shell.run(args, check=False, capture=True)
    return res.returncode, res.stdout or ""


def _latency(url: str, via_proxy: bool) -> _Lat:
    rc, out = _curl(url, via_proxy, "%{time_starttransfer} %{http_code}", body=False)
    if rc != 0:
        return _Lat(None, "ERR")
    parts = out.split()
    if len(parts) < 2:
        return _Lat(None, "ERR")
    try:
        ms = int(round(float(parts[0]) * 1000))
    except ValueError:
        ms = None
    return _Lat(ms, parts[1])


def _trace(url: str, via_proxy: bool) -> dict[str, str] | None:
    rc, out = _curl(url, via_proxy, "", body=True)
    if rc != 0:
        return None
    fields: dict[str, str] = {}
    for line in out.splitlines():
        if "=" in line:
            k, _, v = line.partition("=")
            fields[k] = v
    return fields or None


def _fmt_ms(ms: int | None) -> str:
    return "超时" if ms is None else f"{ms}ms"


def _run_pool(items, worker, label: str):
    """并发跑 worker(item)，带 TTY 进度，返回 {item: result}。"""
    total = len(items)
    tty = keys.interactive_tty()
    if not tty:
        shell.info(f"{label}（{total} 项）…")
    results, done = {}, 0
    with ThreadPoolExecutor(max_workers=min(12, total)) as ex:
        futures = {ex.submit(worker, it): it for it in items}
        for fut in as_completed(futures):
            results[futures[fut]] = fut.result()
            done += 1
            if tty:
                sys.stdout.write(f"\r\033[K  {label}… {done}/{total}")
                sys.stdout.flush()
    if tty:
        sys.stdout.write("\r\033[K")
        sys.stdout.flush()
    return results


def run() -> None:
    shell.header("网络测试")
    via_proxy = _proxy_up()
    if via_proxy:
        shell.info(f"经本地代理 {PROXY_URL} 测试（走 sing-box 出口）。")
    else:
        shell.warn(f"本地代理 {PROXY_URL} 未监听，改用直连测试（结果不代表代理体验）。")

    # 1. 延迟
    lat = _run_pool(_LATENCY_TARGETS, lambda t: _latency(t[2], via_proxy), "延迟测试")
    print()
    last_cat = ""
    for cat, name, _ in _LATENCY_TARGETS:
        if cat != last_cat:
            shell.info(f"【{cat}】")
            last_cat = cat
        r = lat[(cat, name, _)]
        mark = "✓" if r.ms is not None else "✗"
        print(f"  {mark} {name:<12} {_fmt_ms(r.ms):>8}  (HTTP {r.code})")

    # 2. 出口 IP（OpenAI / Claude / Cloudflare）
    print()
    shell.info("【出口 IP / 落地】")
    traces = _run_pool(_TRACE_TARGETS, lambda t: _trace(t[1], via_proxy), "出口探测")
    for name, _ in _TRACE_TARGETS:
        f = traces[(name, _)]
        if not f or "ip" not in f:
            print(f"  ✗ {name:<12} 探测失败")
            continue
        loc = f.get("loc", "?")
        colo = f.get("colo", "")
        extra = f"  [{colo}]" if colo else ""
        print(f"  ✓ {name:<12} {f['ip']:<22} 落地 {loc}{extra}")

    print()
    shell.ok("网络测试完成。")
    keys.read_line("回车返回主菜单… ")
