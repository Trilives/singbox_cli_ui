"""yamlmini 对拍测试：以 PyYAML 为基准（仅测试环境需要，运行时不依赖）。

运行： python3 tests/test_yamlmini.py
若本机无 PyYAML，则跳过对拍、只做基本自洽检查。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))

from singbox_deploy import yamlmini  # noqa: E402

try:
    import yaml  # 测试基准
    _HAS_YAML = True
except ImportError:
    _HAS_YAML = False


CASES = {
    "块式嵌套 vmess+ws": """
proxies:
  - name: hk-01
    type: vmess
    server: a.example.com
    port: 443
    uuid: abc-123
    alterId: 0
    cipher: auto
    tls: true
    network: ws
    ws-opts:
      path: /ray
      headers:
        Host: a.example.com
  - {name: sg-02, type: ss, server: b.com, port: 8388, cipher: aes-256-gcm, password: pw, udp: true}
""",
    "流式序列 alpn": "tls:\n  alpn: [h2, http/1.1]\n  enabled: true\n",
    "布尔/null/数字": "a: true\nb: false\nc: null\nd: 42\ne: 3.14\nf: ~\n",
    "引号含特殊字符": 'x:\n  - {name: "a:b, c", val: \'it\'\'s\'}\n',
    "注释剥离": "a: 1  # 行内注释\n# 整行注释\nb: 2\n",
}


def main() -> int:
    failed = 0

    if _HAS_YAML:
        for name, text in CASES.items():
            a, b = yaml.safe_load(text), yamlmini.load(text)
            ok = a == b
            print(f"[{'OK' if ok else 'FAIL'}] {name}")
            if not ok:
                failed += 1
                print("  PyYAML:", repr(a)[:200])
                print("  mini  :", repr(b)[:200])

        real = Path("/home/ares/Workspace/mihomo-cli-deploy/config.yaml")
        if real.exists():
            text = real.read_text("utf-8")
            a = yaml.safe_load(text)["proxies"]
            b = yamlmini.load(text)["proxies"]
            ok = a == b
            print(f"[{'OK' if ok else 'FAIL'}] 真实配置 proxies ({len(b)} 节点)")
            failed += 0 if ok else 1
    else:
        # 无基准：只验证能解析出 proxies 列表
        d = yamlmini.load(CASES["块式嵌套 vmess+ws"])
        assert isinstance(d["proxies"], list) and len(d["proxies"]) == 2
        print("[OK] 无 PyYAML，基本自洽检查通过")

    print("全部通过" if failed == 0 else f"{failed} 项失败")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
