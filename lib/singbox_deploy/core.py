"""核心资源下载/更新：sing-box 内核 + Web UI + CN 规则集。

移植自参考仓库 update_sing_box_core.sh，改用 Python 标准库：
- GitHub Release API 用 curl 拉取后由 json 模块解析（比 grep/sed 健壮）。
- 解压用标准库 tarfile / zipfile（省掉 unzip 依赖）。
- 下载仍用 curl 子进程：保留"代理优先→直连兜底"通道逻辑、重试、断点续传、完整性校验。

下载相关设置（download_proxy / github_mirror）从 state/customize.json 读取，
未配置时回退环境变量 / 直连。本模块不依赖 customize.py，直接读 JSON，避免循环依赖。
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import tarfile
import tempfile
import zipfile
from pathlib import Path

from . import paths, shell

SING_BOX_REPO = "SagerNet/sing-box"
UI_REPO = "MetaCubeX/metacubexd"
GEOSITE_CN_URL = "https://raw.githubusercontent.com/SagerNet/sing-geosite/rule-set/geosite-cn.srs"
GEOIP_CN_URL = "https://raw.githubusercontent.com/SagerNet/sing-geoip/rule-set/geoip-cn.srs"

GOOGLE_PROBE_URL = "https://www.google.com/generate_204"

_CURL_COMMON = [
    "-fL",
    "--retry", "5",
    "--retry-delay", "2",
    "--retry-all-errors",
    "--connect-timeout", "10",
    "--speed-time", "30",
    "--speed-limit", "1024",
]

_ARCH_MAP = {
    "x86_64": "amd64",
    "aarch64": "arm64",
    "arm64": "arm64",
    "armv7l": "armv7",
    "armv7": "armv7",
    "armv6l": "armv6",
    "armv6": "armv6",
    "i386": "386",
    "i686": "386",
    "riscv64": "riscv64",
    "s390x": "s390x",
}


# --------------------------------------------------------------------------- #
# 设置读取
# --------------------------------------------------------------------------- #
def _settings() -> dict:
    """读 state/customize.json 中与下载相关的字段（容错，缺失返回默认）。"""
    data: dict = {}
    if paths.CUSTOMIZE_FILE.exists():
        try:
            data = json.loads(paths.CUSTOMIZE_FILE.read_text("utf-8"))
        except (json.JSONDecodeError, OSError):
            data = {}
    proxy = data.get("download_proxy") or os.environ.get("DOWNLOAD_PROXY") or ""
    mirror = data.get("github_mirror") or ""
    return {"download_proxy": proxy.strip(), "github_mirror": mirror.strip()}


def _mirror(url: str, mirror: str) -> str:
    """对 GitHub 下载/raw 链接套加速前缀；api.github.com 不套（多数镜像不代理 API）。"""
    if not mirror:
        return url
    if "api.github.com" in url:
        return url
    if url.startswith(("https://github.com/", "https://raw.githubusercontent.com/")):
        return mirror.rstrip("/") + "/" + url
    return url


# --------------------------------------------------------------------------- #
# curl 通道：代理优先 → 直连兜底
# --------------------------------------------------------------------------- #
class _Fetcher:
    def __init__(self, proxy: str):
        self.proxy = proxy
        self._direct_ok: bool | None = None

    def _direct_reachable(self) -> bool:
        if self._direct_ok is None:
            rc = shell.run(
                ["curl", "-fsS", "--noproxy", "*", "--connect-timeout", "5",
                 "--max-time", "10", "-o", os.devnull, GOOGLE_PROBE_URL],
                check=False, capture=True,
            ).returncode
            self._direct_ok = rc == 0
            if self._direct_ok:
                shell.info("直连可达，跳过代理。")
        return bool(self._direct_ok)

    def _channels(self) -> list[str]:
        no_proxy = os.environ.get("SING_BOX_NO_PROXY", "0") == "1"
        if self.proxy and not no_proxy and not self._direct_reachable():
            return ["proxy", "direct"]
        return ["direct"]

    def fetch(self, extra: list[str]) -> None:
        """按通道顺序尝试 curl，首个成功即返回；全失败抛 CommandError。"""
        channels = self._channels()
        last_exc: shell.CommandError | None = None
        for i, ch in enumerate(channels):
            chan_args: list[str] = []
            if ch == "proxy":
                chan_args = ["--proxy", self.proxy]
            elif ch == "direct" and self.proxy:
                chan_args = ["--noproxy", "*"]
            try:
                shell.run(["curl", *_CURL_COMMON, *chan_args, *extra], check=True)
                return
            except shell.CommandError as exc:
                last_exc = exc
                if i < len(channels) - 1:
                    shell.warn(f"  {ch} 通道失败(curl {exc.returncode})，改直连重试…")
        assert last_exc is not None
        raise last_exc

    def read_json(self, url: str) -> dict:
        """拉取 URL 文本并解析 JSON（用于 GitHub API）。"""
        with tempfile.NamedTemporaryFile("r+", suffix=".json", delete=True) as tf:
            extra = ["-sS", "-o", tf.name]
            token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
            if token:
                extra += ["-H", f"Authorization: Bearer {token}"]
            extra.append(url)
            self.fetch(extra)
            tf.seek(0)
            return json.loads(tf.read())


# --------------------------------------------------------------------------- #
# GitHub Release 解析
# --------------------------------------------------------------------------- #
def _arch() -> str:
    machine = os.uname().machine
    return _ARCH_MAP.get(machine, machine)


def _latest_release(fetcher: _Fetcher, repo: str) -> dict:
    return fetcher.read_json(f"https://api.github.com/repos/{repo}/releases/latest")


def _asset_urls(release: dict) -> list[str]:
    return [a.get("browser_download_url", "") for a in release.get("assets", [])]


def _pick_asset(urls: list[str], pattern: str) -> str | None:
    import re
    rx = re.compile(pattern, re.IGNORECASE)
    for u in urls:
        if rx.search(u):
            return u
    return None


# --------------------------------------------------------------------------- #
# 下载 + 缓存校验
# --------------------------------------------------------------------------- #
def _cache_valid(path: Path) -> bool:
    if not path.exists() or path.stat().st_size == 0:
        return False
    name = path.name
    try:
        if name.endswith((".tar.gz", ".tgz")):
            with tarfile.open(path, "r:gz") as t:
                t.getmembers()
        elif name.endswith(".zip"):
            with zipfile.ZipFile(path) as z:
                if z.testzip() is not None:
                    return False
    except (tarfile.TarError, zipfile.BadZipFile, OSError):
        return False
    return True


def _download_to(fetcher: _Fetcher, url: str, out: Path, *, force: bool) -> None:
    part = out.with_suffix(out.suffix + ".part")
    if not force and _cache_valid(out):
        shell.info(f"使用缓存: {out.name}")
        return
    if out.exists():
        shell.info(f"丢弃无效缓存: {out.name}")
        out.unlink(missing_ok=True)
        part.unlink(missing_ok=True)
    resume = ["-C", "-"] if part.exists() and part.stat().st_size > 0 else []
    shell.info(f"下载: {url}")
    fetcher.fetch([*resume, "-o", str(part), url])
    # 校验（仅对压缩包）；非压缩包只查非空
    if part.name.endswith((".tar.gz", ".tgz", ".zip")) and not _cache_valid(part):
        part.unlink(missing_ok=True)
        raise RuntimeError(f"下载文件完整性校验失败: {out.name}")
    if part.stat().st_size == 0:
        part.unlink(missing_ok=True)
        raise RuntimeError(f"下载文件为空: {out.name}")
    part.replace(out)


# --------------------------------------------------------------------------- #
# 部署各组件
# --------------------------------------------------------------------------- #
def update_core(fetcher: _Fetcher | None = None, *, libc: str = "glibc", force: bool = False) -> str:
    """下载并部署 sing-box 内核，返回版本号。"""
    paths.ensure_state_dirs()
    f = fetcher or _make_fetcher()
    s = _settings()
    shell.info("查找最新 sing-box 版本…")
    rel = _latest_release(f, SING_BOX_REPO)
    version = rel.get("tag_name", "").strip()
    urls = _asset_urls(rel)
    arch = _arch()
    url = None
    if libc == "any":
        url = _pick_asset(urls, rf"sing-box-[^/]+-linux-{arch}\.tar\.gz$")
    if not url and libc != "any":
        url = _pick_asset(urls, rf"sing-box-[^/]+-linux-{arch}-{libc}\.tar\.gz$")
    url = url or _pick_asset(urls, rf"sing-box-[^/]+-linux-{arch}\.tar\.gz$")
    url = url or _pick_asset(urls, rf"sing-box-[^/]+-linux-{arch}.*\.tar\.gz$")
    if not url:
        raise RuntimeError(f"未找到架构 {arch} 的 Linux sing-box 资源")

    archive = paths.DOWNLOADS_DIR / Path(url).name
    _download_to(f, _mirror(url, s["github_mirror"]), archive, force=force)

    with tempfile.TemporaryDirectory() as td:
        with tarfile.open(archive, "r:gz") as t:
            t.extractall(td)
        binpath = next((p for p in Path(td).rglob("sing-box") if p.is_file()), None)
        if binpath is None:
            raise RuntimeError("解压后未找到 sing-box 可执行文件")
        paths.BIN_DIR.mkdir(parents=True, exist_ok=True)
        shutil.copy2(binpath, paths.SINGBOX_BIN)
        paths.SINGBOX_BIN.chmod(paths.SINGBOX_BIN.stat().st_mode | stat.S_IEXEC | 0o755)
    paths.SINGBOX_VERSION_FILE.write_text(version + "\n", "utf-8")
    shell.ok(f"内核已部署: {version}")
    return version


def update_ruleset(fetcher: _Fetcher | None = None, *, force: bool = False) -> None:
    """下载 CN 规则集。"""
    paths.ensure_state_dirs()
    f = fetcher or _make_fetcher()
    s = _settings()
    for url, dest in ((GEOSITE_CN_URL, paths.GEOSITE_CN), (GEOIP_CN_URL, paths.GEOIP_CN)):
        cache = paths.DOWNLOADS_DIR / dest.name
        _download_to(f, _mirror(url, s["github_mirror"]), cache, force=force)
        shutil.copy2(cache, dest)
    shell.ok("CN 规则集已更新")


def update_ui(fetcher: _Fetcher | None = None, *, force: bool = False) -> None:
    """下载并部署 Web UI（metacubexd）。"""
    paths.ensure_state_dirs()
    f = fetcher or _make_fetcher()
    s = _settings()
    shell.info("查找最新 Web UI 版本…")
    rel = _latest_release(f, UI_REPO)
    urls = _asset_urls(rel)
    url = _pick_asset(urls, r"(gh-pages|dist).*(\.zip|\.tar\.gz|\.tgz)$") \
        or _pick_asset(urls, r"(\.zip|\.tar\.gz|\.tgz)$")
    if not url:
        raise RuntimeError(f"未从 {UI_REPO} releases 找到 UI 资源")
    archive = paths.DOWNLOADS_DIR / Path(url).name
    _download_to(f, _mirror(url, s["github_mirror"]), archive, force=force)

    with tempfile.TemporaryDirectory() as td:
        _extract(archive, Path(td))
        ui_root = _find_ui_root(Path(td))
        if ui_root is None:
            raise RuntimeError(f"未能定位 UI 根目录: {archive.name}")
        if paths.UI_DIR.exists():
            shutil.rmtree(paths.UI_DIR)
        shutil.copytree(ui_root, paths.UI_DIR)
    shell.ok("Web UI 已部署")


def _extract(archive: Path, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    if archive.name.endswith((".tar.gz", ".tgz")):
        with tarfile.open(archive, "r:gz") as t:
            t.extractall(out_dir)
    elif archive.name.endswith(".zip"):
        with zipfile.ZipFile(archive) as z:
            z.extractall(out_dir)
    else:
        raise RuntimeError(f"不支持的压缩格式: {archive.name}")


def _find_ui_root(extract_dir: Path) -> Path | None:
    indexes = list(extract_dir.rglob("index.html"))
    for idx in indexes:
        d = idx.parent
        if (d / "assets").is_dir() or (d / "_nuxt").is_dir():
            return d
    return indexes[0].parent if indexes else None


def _make_fetcher() -> _Fetcher:
    s = _settings()
    if s["download_proxy"]:
        shell.info(f"使用下载代理: {s['download_proxy']}")
    return _Fetcher(s["download_proxy"])


def download_all(*, libc: str = "glibc", force: bool = False) -> str:
    """初始化用：下载内核 + 规则集 + UI，返回内核版本。"""
    f = _make_fetcher()
    version = update_core(f, libc=libc, force=force)
    update_ruleset(f, force=force)
    update_ui(f, force=force)
    return version


# --------------------------------------------------------------------------- #
# 独立调用入口
# --------------------------------------------------------------------------- #
def run(argv: list[str] | None = None) -> int:
    import argparse
    p = argparse.ArgumentParser(prog="singbox_deploy.core", description="下载/更新 内核+UI+规则集")
    p.add_argument("--libc", default="glibc", choices=["glibc", "musl", "any"])
    p.add_argument("--force", action="store_true", help="忽略下载缓存")
    p.add_argument("--only", choices=["core", "ui", "ruleset"], help="只更新某一项")
    args = p.parse_args(argv)
    try:
        if args.only == "core":
            update_core(libc=args.libc, force=args.force)
        elif args.only == "ui":
            update_ui(force=args.force)
        elif args.only == "ruleset":
            update_ruleset(force=args.force)
        else:
            download_all(libc=args.libc, force=args.force)
    except (RuntimeError, shell.CommandError) as exc:
        shell.error(str(exc))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
