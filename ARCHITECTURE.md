# sing-box CLI 部署系统 · 架构

交互式 CLI 部署 / 管理 sing-box 的零依赖系统。一个入口 `singbox.sh`，全流程交互完成
**初始化 / 更改配置 / 网络测试 / 卸载**。本文描述已落地的架构；上手用法见 [README](README.md)。

> 姊妹项目：[mihomo-cli-deploy](https://github.com/Trilives/mihomo-cli-deploy) —— 同一套
> 交互骨架的 Mihomo（Clash.Meta）版，直接消费机场原生 Clash 配置。

---

## 1. 设计取向

| 决策 | 选择 | 理由 |
| --- | --- | --- |
| 语言 | Python 为主，`singbox.sh` 仅瘦入口 | 模块化、可单独 `-m` 调用 |
| 运行时依赖 | **仅系统 `python3` 标准库** | 部署机常无代理，pip 装包慢/易失败 |
| YAML 解析 | 自带 `yamlmini`（替代 PyYAML） | 零依赖解析 Clash 订阅 |
| 转换引擎 | 本地解析为主 + subconverter 兜底 | 凭证不外泄，难解析的链接才上云 |
| 网络下载 | 外部 `curl` 子进程 | 稳定、沿用原脚本习惯 |
| 界面 | stdlib 自绘 TUI（termios + ANSI） | 零依赖；非 TTY 自动回退编号菜单 |
| 提权 | 按需 `sudo`（`shell.run_root`） | 普通用户启动，需 root 时自动提权 |

**硬约束（零依赖）**：不使用虚拟环境、不装任何 pip 包、尤其不用 PyYAML。网络本身是
通的（国内镜像/直连），只是出海慢——`curl` 下载、subconverter 兜底等"有网即可"的能力照常使用。

**范围**：仅 Linux + systemd；单内核（sing-box）；单 active 订阅模型。

---

## 2. 目录结构

```
Singbox/
├── singbox.sh                  # 瘦入口：环境检查 → PYTHONPATH=lib exec python3 -m singbox_deploy
├── ARCHITECTURE.md             # 本文
├── README.md
├── LICENSE                     # MIT
├── lib/singbox_deploy/         # Python 主体（模块可单独 -m 调用）
│   ├── __main__.py             # 入口分发：init / modify / nettest / uninstall / update
│   ├── paths.py                # 统一路径常量
│   ├── shell.py                # 子进程 / 日志 / 彩色输出 / run_root 提权
│   ├── errors.py               # 共享异常（Cancelled / SaveExit）
│   ├── keys.py                 # 可中断终端输入（termios 原始模式 + 等宽处理）
│   ├── menu.py                 # 自绘 TUI 组件：select / multiselect / ask / confirm（含滚动视口）
│   ├── tx.py                   # 事务 / 回退引擎（ESC 或异常时 LIFO 回滚）
│   ├── yamlmini.py             # 标准库实现的极简 Clash YAML 解析器
│   ├── flows/                  # 入口流程编排
│   │   ├── init.py             # 初始化全流程
│   │   ├── modify.py           # 更改配置全流程（会话级事务）
│   │   ├── nettest.py          # 网络测试（延迟 + 出口 IP）
│   │   ├── uninstall.py        # 卸载（多选清单）
│   │   └── common.py           # 流程间共享的交互（新增订阅询问等）
│   ├── core.py                 # 下载/更新 内核 + Web UI + CN 规则集
│   ├── service.py              # systemd 注册/删除/重启，同步配置到 /etc/sing-box/
│   ├── resilience.py           # 网络自愈（NM 钩子 + watchdog 定时器）
│   ├── timer.py                # 每周自动更新定时器
│   ├── node_select.py          # 交互切换/固定节点（运行时 Clash API + 实时测速）
│   ├── proxyenv.py             # TUN 关闭时写入 shell 代理环境变量
│   ├── firewall.py             # 局域网代理时放行/撤销防火墙端口
│   ├── customize.py            # 定制层：分流/直连/TUN/DNS/面板的字段编辑与注入
│   └── subscription/           # 订阅子系统
│       ├── manager.py          # 命名订阅 增/删/改名/切换/列表/刷新
│       ├── fetch.py            # 下载订阅原始内容
│       ├── detect.py           # 来源类型识别（base64 / singbox / clash）
│       ├── b64.py              # base64：subconverter 解析 + 应急本地解析
│       └── convert.py          # 三来源 → sing-box 配置（主转换器）
├── templates/                  # systemd unit / NM 钩子 / healthcheck 模板
├── tests/                      # yamlmini / 转换器 对拍测试（PyYAML 仅测试用）
└── state/                      # 运行期产物（.gitignore，全部本地生成）
```

`state/` 关键文件：`subscriptions/<name>/{meta.json,raw.<ext>,config.json}`、`active`（当前生效
订阅名）、`config.json`（生效配置 = active 订阅的拷贝）、`customize.json`（定制层）。

---

## 3. 定制层 `state/customize.json`

一等配置功能：既可手改 JSON，也可经「更改配置 → 编辑定制层」交互式增删改。每次转换/刷新
订阅都读取最新值。字段分三类：

- **列表**：`ai_domain_suffixes` / `streaming_domain_suffixes` / `direct_domain_suffixes` /
  `local_bypass_domains` / `route_exclude_ip_cidrs`（TUN 排除网段）/ `bypass_process_names` /
  `tun_exclude_uids` / `prefer_keywords`（SG）/ `hk_prefer_keywords`（HK）。
- **开关**：`enable_tun` / `lan_panel` / `lan_proxy` / `generate_sg_groups` /
  `generate_hk_groups` / `base64_local_fallback`。
- **标量**：`bootstrap_dns_server` / `bootstrap_dns_port` / `default_outbound` /
  `subconverter_backend` / `github_mirror` / `download_proxy` / `github_token`
  （GitHub API 认证 Token，突破匿名 60 次/小时限速；输入不回显，编辑器里仅显示已设置/未设置）。

编辑器为**缓冲式**：列出全部字段（常用项前置），`esc` 保存并退出才写盘，`^R` 放弃本次修改；
字段多于一屏时菜单**滑动显示**（仅渲染选中项附近的窗口 + 上/下剩余条数提示）。

---

## 4. 交互流程

```
主菜单 → 初始化 / 更改配置 / 网络测试 / 卸载
```

- **初始化**（`flows/init.py`）：下载代理 → TUN/局域网开关 → 下载内核/UI/规则集 →
  增强配置 → 添加首个订阅 → 注册服务 →（可选）网络自愈 / 每周更新 →（可选）切换/固定节点。
- **更改配置**（`flows/modify.py`）：整个会话包在一个 `Transaction` 里——配置类改动（订阅 /
  定制层）均为临时，`esc` 保存提交、`^R` 回退；系统类操作（更新内核 / 节点切换 / 服务 /
  自愈 / 定时器）标 `※即时`。**订阅链接变化时**（新增设为生效 / 切换 / 刷新生效订阅）
  完成后交互提示是否进入「切换 / 固定节点」（即菜单第三项）。
- **网络测试**（`flows/nettest.py`）：经本地代理并发测一批目标的 TTFB，并对多方向探测出口 IP / 落地。
- **卸载**（`flows/uninstall.py`）：多选清单逐项执行（服务 / 自愈 / 定时器 / 产物 / 全部状态）。

---

## 5. 订阅与转换引擎

**核心理念**：所有"生成 sing-box 结构"的活收敛到一个可控的本地 clash→singbox 转换器
（`convert.py`，移植自参考实现，仅把 `yaml.safe_load` 换成 `yamlmini.load`）；难且易错的
"节点链接解析"交给云端 subconverter。三种来源最终体验一致：

| 来源 | 路径 | 隐私 |
| --- | --- | --- |
| **Clash 订阅**（★推荐） | `raw(yaml) →` 本地转换器 | 全程本地，凭证不外泄 |
| **sing-box 直链** | `raw(json) →` 校验 / 可选注入定制层 | 本地 |
| **通用 base64** | `raw(b64) →` subconverter(clash) `→` 本地转换器 | 凭证发往后端，可换自建 |

后端取 `customize.json.subconverter_backend`（默认公开后端 `https://sub.v1.mk`）。base64 的
应急本地解析默认关闭、带风险提示，仅无可用后端时显式开启。

---

## 6. 可中断与回退（ESC + 事务）

- **取消触发**：任意交互处 `ESC`（或 Ctrl-C / EOF）抛 `Cancelled`。`keys.py` 在 TTY 下用
  termios 原始模式逐键读取：单独 ESC 取消，方向键等转义序列忽略，正确处理中文等宽退格。
- **事务回退**：会改动系统状态的流程包在 `Transaction` 内，`backup_file` / `track_path` /
  `add_undo` 登记回退动作；正常走完即 commit，中途 `Cancelled`/异常按 LIFO 逆序回滚，单项
  失败不阻断其余，最后汇总。`Cancelled` 被事务吞掉并回退后平滑返回上层菜单，不退出程序。

---

## 7. 第三方资产

本项目不打包任何二进制/UI/规则集，全部运行时按需下载（见 `core.py`），各自保留原始许可证：
sing-box 内核（SagerNet/sing-box, GPL-3.0）、Web 面板（MetaCubeX/metacubexd）、CN 规则集
（sing-geosite / sing-geoip）、订阅转换后端（公共 subconverter 实例）。本项目代码以 MIT 发布。
