"""转换器回归测试：与参考实现对拍 + sing-box check（均为可选，缺依赖则跳过）。

运行： python3 tests/test_convert.py
"""

import copy
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

from singbox_deploy import customize  # noqa: E402
from singbox_deploy.subscription import convert  # noqa: E402

REF_DIR = Path("/home/ares/Workspace/mihomo-cli-deploy/sing_box")
REF_CONV = REF_DIR / "Script/Enhance/clash_nodes_to_singbox.py"
REAL_CONFIG = Path("/home/ares/Workspace/mihomo-cli-deploy/config.yaml")


def _enabled_cfg() -> dict:
    cfg = dict(customize.DEFAULTS)
    cfg["generate_sg_groups"] = True
    cfg["generate_hk_groups"] = True
    return cfg


def _norm(c: dict) -> dict:
    c = copy.deepcopy(c)
    for rs in c.get("route", {}).get("rule_set", []):
        rs["path"] = "<RS>"
    c["experimental"]["clash_api"]["external_ui"] = "<UI>"
    return c


def test_against_reference() -> bool:
    try:
        import yaml  # noqa: F401
    except ImportError:
        print("[SKIP] 无 PyYAML，跳过参考对拍")
        return True
    if not (REF_CONV.exists() and REAL_CONFIG.exists()):
        print("[SKIP] 参考实现/真实配置缺失，跳过对拍")
        return True

    import yaml
    spec = importlib.util.spec_from_file_location("ref_conv", REF_CONV)
    ref = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ref)

    text = REAL_CONFIG.read_text("utf-8")
    proxies = yaml.safe_load(text)["proxies"]
    used = set(ref.RESERVED_TAGS)
    nodes = []
    for p in proxies:
        o, r = ref.convert_proxy(p, used)
        if not r:
            nodes.append(o)
    ref_cfg, _ = ref.build_singbox_config(
        nodes,
        ref.parse_prefer_keywords(ref.DEFAULT_PREFER),
        ref.parse_prefer_keywords(ref.DEFAULT_HK_PREFER),
        "Proxy", Path("sing_box/config.json"), ref.DEFAULT_CUSTOM_CONFIG, False,
    )
    my_cfg, info = convert.clash_to_singbox(text, customize.to_custom_config(_enabled_cfg()))
    ok = _norm(ref_cfg) == _norm(my_cfg)
    print(f"[{'OK' if ok else 'FAIL'}] 与参考实现一致（{info['converted']}/{info['total']} 节点）")
    return ok


def test_singbox_check() -> bool:
    sb = REF_DIR / "sing-box"
    if not (sb.exists() and REAL_CONFIG.exists()):
        print("[SKIP] 无 sing-box 二进制/真实配置，跳过 check")
        return True
    text = REAL_CONFIG.read_text("utf-8")
    cfg, _ = convert.clash_to_singbox(text, customize.to_custom_config(_enabled_cfg()))
    for rs in cfg["route"]["rule_set"]:
        rs["path"] = str(REF_DIR / "ruleset" / ("geosite-cn.srs" if rs["tag"] == "geosite-cn" else "geoip-cn.srs"))
    cfg["experimental"]["clash_api"]["external_ui"] = str(REF_DIR / "ui")
    tmp = ROOT / "tests" / ".gen_check.json"
    tmp.write_text(json.dumps(cfg, ensure_ascii=False), "utf-8")
    try:
        rc = subprocess.run([str(sb), "check", "-c", str(tmp)], capture_output=True, text=True)
    finally:
        tmp.unlink(missing_ok=True)
    ok = rc.returncode == 0
    print(f"[{'OK' if ok else 'FAIL'}] sing-box check" + ("" if ok else f"\n  {rc.stderr.strip()}"))
    return ok


def main() -> int:
    results = [test_against_reference(), test_singbox_check()]
    print("全部通过" if all(results) else "存在失败")
    return 0 if all(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
