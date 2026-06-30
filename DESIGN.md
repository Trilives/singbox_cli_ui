# sing-box CLI 部署系统 · 设计文档

> 本仓库是对 `mihomo-cli-deploy/sing_box` 的重构。原仓库保持不动，仅作参考。
> 以 `Singbox/` 为根目录，构建一个交互式 CLI 的 sing-box 部署系统。

---

## 1. 目标与范围

把原仓库零散的 7 个 sh + 2 个 py 脚本，重构为一个**统一交互入口 + 模块化 Python 后端**的部署系统。

核心诉求：

- 根目录一个 `singbox.sh`，运行后进入交互式全流程。
- 第一层选择：**初始化 / 更改配置 / 卸载所有服务**。
- 每个入口下有第二层子流程。
- 订阅管理升级：支持 3 种订阅来源、命名保存、一键切换。

**不在本期范围**：Windows/macOS 支持（仅 Linux + systemd）、图形界面、多内核（仅 sing-box）。

---

## 2. 技术选型（已决策）

| 决策项 | 选择 |
| --- | --- |
| 实现语言 | **Python 为主**，根目录 `singbox.sh` 仅做瘦入口 |
| 入口定位 | 瘦入口 + 模块化子模块，每个功能可单独调用 |
| 转换引擎 | **本地解析为主 + subconverter 后端兜底** |
| 定制注入 | **由用户每次选择**是否注入定制层（按订阅持久化） |
| 运行时依赖 | **仅 base Python3 标准库**，禁止虚拟环境、禁止任何 pip 第三方包 |

**硬约束（零依赖）**：部署机在配置时通常没有网络代理，访问全球网络很慢，
用 pip 安装第三方包慢且易失败（PyPI/GitHub 拉取卡顿）。因此不引入任何第三方包。
注意：网络本身是通的（经国内镜像/直连），只是出海慢——`curl` 下载、subconverter
兜底等"有网即可"的能力照常可用，无需为它们做离线降级。因此：

- **不使用虚拟环境**，直接用系统 `python3` 运行。
- **不依赖任何第三方库**——尤其 **不用 `PyYAML`**。
- Clash 订阅是 YAML，故自带一个**标准库实现的极简 YAML 解析器**（`yamlmini.py`），
  只覆盖 Clash 订阅会出现的结构（`proxies` 列表、块/流式 mapping、标量/列表），
  不追求通用 YAML 规范。base64（`base64`+`json`）、sing-box 直链（`json`）天然零依赖。
- 网络下载统一走外部 `curl`（子进程），不引入 Python HTTP 库依赖之外的东西
  （标准库 `urllib` 可用，但 `curl` 更稳，沿用原脚本习惯）。

> 迁移利好：参考的 `clash_nodes_to_singbox.py` **唯一第三方依赖就是 `import yaml`**
> （仅 `yaml.safe_load` 一处），其余全为标准库。替换该处为 `yamlmini` 即可零依赖移植。

环境依赖：Linux + systemd；系统自带 `python3`；`curl`、`tar`；TUN/服务需 root。
subconverter 兜底为**可选**：未配置后端时，仅本地解析路径可用，转换失败则提示。

**权限（sudo）策略**：不强制整脚本以 root 运行。普通用户启动 `./singbox.sh`，遇到需要
root 的操作（systemd 注册/删除、写 `/etc/sing-box/`、NM 钩子、TUN 试跑）时，由
`shell.run_root()` 自动加 `sudo` 执行；首次触发先 `sudo -v` 弹出密码输入（会话内缓存），
并提示用户也可直接 `sudo ./singbox.sh` 启动以免中途输密码。sudo 授权失败按取消处理。

**界面（TUI）选择**：采用 **stdlib 自绘 TUI**——方向键导航、反显高亮、边框盒子、颜色，
观感接近 Claude Code / opencode，且**零第三方依赖**（基于 `keys.py` 的 termios 原始
模式 + ANSI）。非 TTY（管道/重定向/测试）自动回退到编号列表 + 文本输入。
不引入 rich/textual 等库——正因无代理装包困难，与零依赖硬约束冲突。

---

## 3. 目录结构

```
Singbox/
├── singbox.sh                    # 瘦入口：检查 root/依赖/python → exec python3 -m singbox_deploy
├── DESIGN.md                    # 本文档
├── README.md
├── .gitignore                   # 忽略 state/ 全部运行期产物
│                                #（无 requirements.txt：零第三方依赖）
│
├── lib/
│   └── singbox_deploy/          # Python 主体（模块化，每个可 python -m 单独跑）
│       ├── __init__.py
│       ├── __main__.py          # 主菜单分发：init / modify / uninstall
│       ├── paths.py             # 统一路径常量（根、state、etc 目标等）
│       ├── shell.py             # 子进程/日志/确认/彩色输出等公共工具
│       ├── errors.py            # 共享异常（Cancelled）——独立以避免循环导入
│       ├── keys.py              # 可中断终端输入：ESC 受控取消（termios 原始模式）
│       ├── tx.py                # 事务/回退引擎：取消或出错时回滚已应用改动
│       ├── yamlmini.py          # 标准库实现的极简 Clash YAML 解析器（替代 PyYAML）
│       ├── menu.py              # 交互菜单组件（单选/多选/输入/确认，走 keys 可 ESC）
│       │
│       ├── flows/               # 三大入口的流程编排
│       │   ├── init.py          # 初始化全流程
│       │   ├── modify.py        # 更改配置全流程
│       │   └── uninstall.py     # 卸载全流程
│       │
│       ├── core.py              # 下载/更新 内核 + Web UI + CN 规则集
│       ├── service.py           # systemd 注册/删除/重启，同步配置到 /etc/sing-box/
│       ├── resilience.py        # 网络自愈（NM 钩子 + watchdog 定时器）
│       ├── timer.py             # 每周自动更新定时器
│       ├── node_select.py       # 交互切换/固定首选节点（含运行时 Clash API）
│       ├── customize.py         # 定制注入层（分流/TUN/DNS/Clash API/UI）
│       │
│       └── subscription/        # 订阅子系统（重点）
│           ├── __init__.py
│           ├── manager.py       # 命名订阅 增/删/改名/切换/列表/刷新
│           ├── fetch.py         # 下载订阅原始内容
│           ├── detect.py        # 来源类型识别（base64 / singbox / clash）
│           ├── b64.py           # base64：subconverter 解析 + 应急本地解析
│           └── convert.py       # 三来源 → singbox 配置
│
├── templates/                   # 安装到系统的文件模板
│   ├── sing-box.service.tmpl    # systemd unit
│   ├── nm-dispatcher.sh.tmpl    # NetworkManager 钩子
│   ├── watchdog.service.tmpl
│   ├── watchdog.timer.tmpl
│   ├── healthcheck.sh           # watchdog 探针（独立文件）
│   └── update.timer.tmpl        # 每周更新定时器
│
└── state/                       # 运行期产物（.gitignore，全部本地生成）
    ├── bin/
    │   ├── sing-box             # 内核可执行
    │   └── sing-box.version
    ├── ui/                      # Web UI 静态资源
    ├── ruleset/                 # geosite-cn.srs / geoip-cn.srs
    ├── downloads/               # 下载缓存
    ├── subscriptions/
    │   └── <name>/
    │       ├── meta.json        # 订阅元数据
    │       ├── raw.<ext>        # 原始订阅内容缓存
    │       └── config.json      # 由该订阅生成的 sing-box 配置
    ├── active                   # 文本文件，内容为当前生效订阅名
    ├── config.json             # 当前生效配置（= active 订阅的 config.json 拷贝）
    └── customize.json           # 全局定制层配置（AI/流媒体/直连/TUN 等）
```

> 设计取舍：用 `lib/singbox_deploy/` 作为可导入包，`singbox.sh` 内
> `PYTHONPATH=lib exec python3 -m singbox_deploy "$@"`，免安装即可运行；
> 同时每个模块支持 `python3 -m singbox_deploy.core --help` 单独调用，满足"模块可单独跑"。

---

## 4. 状态文件格式

### 4.1 `state/subscriptions/<name>/meta.json`

```jsonc
{
  "name": "my-airport",          // 用户命名，同时是目录名（slug 化）
  "url": "https://...",          // 订阅链接
  "source_type": "clash",        // base64 | singbox | clash | auto
  "customize": true,             // 是否注入定制层（用户选择，持久化）
  "converter": "local",          // 实际成功使用的引擎：local | subconverter
  "created_at": "2026-06-29T12:00:00Z",
  "updated_at": "2026-06-29T12:00:00Z",
  "last_node_count": 37          // 上次转换得到的节点数（用于体检）
}
```

### 4.2 `state/active`

单行文本，内容为当前生效订阅名。切换订阅 = 改写此文件 + 拷贝其 `config.json` 到 `state/config.json` + 同步到 `/etc/sing-box/` + 重启服务。

### 4.3 `state/customize.json`（定制层 / 可交互编辑）

迁移自原 `clash_nodes_to_singbox_config.json`，字段保持兼容。这是一个**一等配置功能**：
既可手动编辑该 JSON，也可经菜单「编辑定制层」交互式增删改每个字段（见 5.5）。

| 字段 | 类型 | 说明 | 默认 |
| --- | --- | --- | --- |
| `ai_domain_suffixes` | list[str] | 走 `AI` 出站的域名后缀 | 内置常用集 |
| `streaming_domain_suffixes` | list[str] | 走 `Streaming` 出站的域名后缀 | 内置常用集 |
| `direct_domain_suffixes` | list[str] | 直连域名后缀（非空时生成 `Direct` 组）| `[]` |
| `local_bypass_domains` | list[str] | 本地直连域名 | `["localhost"]` |
| `route_exclude_ip_cidrs` | list[str] | TUN 自动路由排除网段 | 私有网段集 |
| `bypass_process_names` | list[str] | 直连进程名（如 `tailscaled`）| `[]` |
| `tun_exclude_uids` | list[int] | TUN `exclude_uid`，指定用户绕过 | `[]` |
| `lan_panel` | bool | 面板监听 `0.0.0.0:9090` 并放行私有网络 | `false` |
| `bootstrap_dns_server` | str | 引导 DNS 地址（`"dhcp"` 跟随系统）| `223.5.5.5` |
| `bootstrap_dns_port` | int | 引导 DNS 端口 | `53` |
| `prefer_keywords` | list[str] | 生成 `SG-Auto`/`SG-Fallback` 的关键词 | 新加坡集 |
| `hk_prefer_keywords` | list[str] | 生成 `HK-Auto`/`HK-Fallback` 的关键词 | 香港集 |
| `generate_sg_groups` | bool | 是否生成新加坡地区组 | `true` |
| `generate_hk_groups` | bool | 是否生成香港地区组 | `true` |
| `default_outbound` | str | 默认主出站 | `Proxy` |
| `subconverter_backend` | str | base64 转换后端（默认公开后端，可换自建）| `https://sub.v1.mk` |
| `base64_local_fallback` | bool | 无后端时允许应急本地解析 base64（有风险）| `false` |
| `github_mirror` | str | GitHub 下载加速前缀（空=直连，逃生口）| `""` |
| `download_proxy` | str | 下载用局域网代理（空=不走代理），初始化时可填 | `""` |

> 原脚本里写死在文件顶部的常量（`GENERATE_SG_GROUPS` / `GENERATE_HK_GROUPS`、
> `--prefer` / `--hk-prefer` / `--default-outbound` 命令行参数）一并收编为此处可配字段，
> 不再需要改源码或记命令行。每次转换/刷新订阅都读取最新 `customize.json`。

---

## 5. 交互流程（菜单树）

运行 `./singbox.sh` →

```
sing-box 部署系统
─────────────────
请选择操作：
  1) 初始化（首次部署）
  2) 更改配置
  3) 卸载所有服务
  0) 退出
```

### 5.1 初始化（flows/init.py）

```
1. 环境检查（root / python / curl / tar / systemd）
2. 询问局域网代理地址（如 http://192.168.1.10:7890，回车留空=不走代理）
   - 仅用于下载内核/UI/规则集，写入 customize.json 的 download_proxy 供后续更新复用
3. 下载内核 + Web UI + CN 规则集    [core.download_all，按 download_proxy 走 curl -x]
4. 添加首个订阅                       [subscription 子流程, 见 5.4]
   - 输入名称
   - 选择来源类型：1)Clash订阅(推荐)  2)机场singbox直链  3)通用base64
   - 输入订阅链接
   - 选择是否注入定制层（y/N）
   - 拉取 → 转换（含 fallback）→ sing-box check 校验
5. 设为 active 并写入 state/config.json
6. 注册 systemd 服务                  [service.install]
7. 可选：安装网络自愈                  [resilience.install]
8. 可选：安装每周自动更新              [timer.install]
9. 启动并显示状态 + UI 访问提示
```

### 5.2 更改配置（flows/modify.py）

```
请选择要更改的内容：
  1) 订阅管理        → 增 / 删 / 改名 / 切换 active / 刷新   [subscription.manager]
  2) 切换 / 固定节点 → 交互选节点（运行时 Clash API + 配置持久化）[node_select]
  3) 编辑定制层      → 改分流/直连/TUN 排除/LAN 面板等        [customize]
  4) 更新内核/UI/规则集 → 手动触发更新后重装服务            [core + service]
  5) 服务设置        → 改服务名 / 重启 / 查看状态            [service]
  6) 网络自愈设置    → 安装/调间隔/卸载                       [resilience]
  7) 每周更新定时器  → 安装/卸载                              [timer]
  0) 返回
```

任何改动配置的操作完成后，统一执行：重新生成 active 配置 → 校验 → 同步 `/etc/sing-box/` → 重启服务。

### 5.5 编辑定制层（customize.py · 交互式字段编辑器）

这是「更改配置 → 3) 编辑定制层」的子流程，把 `state/customize.json` 的每个字段做成
可交互增删改的菜单，免去手改 JSON。改完询问是否立即重生成 active 配置并重启。

```
编辑定制层（customize.json）
当前值预览：
  分流 · AI 域名后缀          (23 条)
  分流 · 流媒体域名后缀        (18 条)
  分流 · 直连域名后缀          (空)
  TUN · 排除网段              (12 条)
  TUN · 排除 UID             [997]
  进程 · 直连进程名           [tailscaled]
  地区组 · 新加坡关键词        开 · 5 词
  地区组 · 香港关键词          开 · 4 词
  面板 · LAN 暴露             false
  DNS · 引导服务器            223.5.5.5:53
  兜底 · subconverter 后端     (未配置)

请选择要编辑的项（回车返回）：_
```

- **列表类字段**（域名后缀 / 网段 / 进程名 / UID / 关键词）：进入后可
  `查看 / 添加一条 / 删除一条 / 批量粘贴替换 / 恢复默认`。
- **布尔类字段**（`lan_panel` / `generate_*_groups`）：直接切换开关。
- **标量字段**（`bootstrap_dns_*` / `default_outbound` / `subconverter_backend`）：输入新值，带校验。
- 保存即写回 `customize.json`；退出时提示"已变更，是否立即应用到 active 订阅并重启？"。

> 初始化流程（5.1）在添加首个订阅前，也提供一次"是否现在调整定制层"的可选入口，
> 默认沿用内置默认值，回车跳过。

### 5.3 卸载（flows/uninstall.py）

```
将卸载以下内容（按需勾选）：
  [x] systemd 服务
  [x] 网络自愈（NM 钩子 + watchdog 定时器）
  [x] 每周更新定时器
  [ ] 清理产物（内核 / UI / 下载缓存 / 规则集）
  [ ] 清理所有订阅与配置（含 state/）

确认后逐项执行，最后报告结果。
```

### 5.4 订阅子流程（subscription 三来源）

```
来源类型决定转换路径（顺序即推荐优先级）：

[1] Clash 订阅（★推荐：兼容性最好，纯本地转换、不外泄凭证）
      raw(yaml) → 本地 clash→singbox 转换（迁移自 clash_nodes_to_singbox.py）

[2] 机场 sing-box 直链
      raw(json) → sing-box check 校验
      customize=true → 抽取 outbounds 注入定制模板
      customize=false → 原样使用（最多补 clash_api）

[3] 通用 base64 订阅 —— 默认走云端 subconverter 解析（本地手写解析风险大）
      raw(base64) → subconverter(target=clash) → 本地 clash→singbox 转换器(+定制层)
      · 云端只负责"节点链接解析"这件难事，最终 singbox 结构与分组/分流/TUN
        定制仍由可控的本地转换器生成，与 [1] 共用同一条成熟路径。
      · 后端取自 customize.json 的 subconverter_backend（默认 sub.v1.mk）。
      · 应急选项（默认关闭、带风险提示）：本地直接解析 base64 节点 URI →
        singbox，仅在无可用后端时由用户显式开启，可能漏字段/转错。

转换成功后：
  - 写 <name>/config.json 与 <name>/meta.json
  - sing-box check 校验
  - 若用户选择，设为 active
```

> **隐私权衡**：[1] Clash 全程本地、不外泄，故列为推荐首选。[3] base64 把订阅发给
> 第三方后端 = 把所有节点凭证交给它，存在泄露风险；默认用社区可信公开后端开箱即用，
> 隐私敏感者可在定制层换成**自建后端**（docker 一条命令，可跑在局域网那台代理机上）。
> 因此"机场同时给 base64 和 Clash 两种链接时，优先选 Clash"。

---

## 6. 模块接口约定

每个模块暴露 `run(args)` 供 `python -m singbox_deploy.<mod>` 单跑，同时暴露纯函数供 flows 调用。关键签名：

```python
# core.py
def download_all(force=False) -> None
def update_core() -> str          # 返回新版本号
def update_ui() -> None
def update_ruleset() -> None

# subscription/manager.py
def add(name, url, source_type, customize) -> Subscription
def remove(name) -> None
def rename(old, new) -> None
def switch(name) -> None          # 改 active + 同步 + 重启
def refresh(name) -> Subscription # 重新拉取+转换
def list() -> list[Subscription]
def get_active() -> Subscription | None

# subscription/convert.py
def to_singbox(raw: bytes, source_type: str, customize: bool) -> dict
    # base64 → subconverter(clash) → 本地主转换器；clash → 本地主转换器；
    # singbox 直链 → 校验/注入；返回 sing-box 配置 dict（见 §7）

# service.py
def install(name="sing-box", start=True, allow_lan=False) -> None
def remove(name="sing-box") -> None
def sync_and_restart(name="sing-box") -> None   # 拷 config 到 /etc + restart

# resilience.py / timer.py
def install(...) / def remove(...)

# node_select.py
def select(config_path, group="Proxy") -> None  # 固定首选 + 运行时 API 切换

# customize.py
def load() -> dict        # 读 state/customize.json（缺字段补默认）
def save(cfg: dict) -> None
def edit() -> None        # 交互式字段编辑器（见 5.5），改完询问是否应用
def apply(base_config: dict, cfg: dict) -> dict  # 注入分流/TUN/DNS/clash_api
```

### 6.1 可中断与回退（ESC + 事务）

**目标：整个配置流程随时可受控中止，并自动回退已应用的改动**，不留半成品状态。

- **取消触发**：任意交互处按 **ESC**（或 Ctrl-C / 回车留空返回 / EOF）抛 `Cancelled`。
  `keys.py` 在 TTY 下用 termios 原始模式逐键读取：单独 ESC 触发取消，方向键等
  转义序列（`\x1b[..`）忽略；正确处理中文等宽字符退格。非 TTY（管道/测试）回退
  到标准 `input()`，EOF 视为取消。
- **事务回退**：每个会改动系统状态的流程包在 `Transaction` 内：
  ```python
  with Transaction("初始化") as t:
      t.backup_file(CONFIG_FILE)               # 改文件前登记快照
      write_config(...)
      t.add_undo("卸载服务", lambda: service.remove(name))
      service.install(name)
  # 正常走完 → commit（不回退）；中途 Cancelled/异常 → 按 LIFO 回退已登记 undo
  ```
  - `backup_file(path)`：记录改动前内容（或"原本不存在"），回退时还原/删除。
  - `track_path(path)`：登记将新建的文件/目录，回退时若原本不存在则删除。
  - `add_undo(desc, fn)`：登记任意自定义回退（卸载服务、还原 active 指针、
    重启回旧配置等）。
  - 回退按逆序执行，单项失败不阻断其余，最后汇总报告。
- **粒度**：三大入口的每个"会落盘/动服务"的子操作都登记对应 undo；
  `Cancelled` 被 `Transaction` 吞掉并回退后，平滑返回上层菜单，不退出程序。

---

## 7. 转换引擎细节

**核心理念：所有"生成 sing-box 结构"的活都收敛到一个可控的本地 clash→singbox 转换器；
难且易错的"节点链接解析"交给云端 subconverter。** 这样定制层(分组/分流/TUN)只在一处实现，
三种来源体验一致。

- **本地 clash→singbox（主转换器）**：移植原 `clash_nodes_to_singbox.py`（1261 行）的协议转换与分组策略（支持 anytls/trojan/ss/vmess/vless/hysteria2/tuic/socks/http），拆成 `convert.py` 内可测试的纯函数。**唯一改动是把 `yaml.safe_load` 换成 `yamlmini.load`**，其余逻辑原样保留。Clash 来源与 base64 来源最终都经此生成 singbox。
- **base64 默认 → 云端 subconverter**：`raw(base64) → subconverter(target=clash) → 本地主转换器`。后端取 `customize.json.subconverter_backend`（默认 `https://sub.v1.mk`，支持 Reality/AnyTLS/TUIC 等新协议）；可换自建后端。失败则提示换后端或开启应急本地解析。
- **本地 base64→singbox（应急，默认关闭）**：直接解析 `vmess://`/`vless://`/`ss://`/`trojan://`/`hysteria2://`/`tuic://` 等链接为 outbound。**风险高（格式杂、易漏字段），仅在无可用后端时由用户显式开启**，带警告。
- **sing-box 直链**：仅下载 + `sing-box check`；customize=true 时抽 outbounds 注入定制模板。
- 凡经本地主转换器的产物统一进入 `customize.apply()`（若 customize=true）。

**可信公开后端（社区在用，可填入 `subconverter_backend`）**：
`https://sub.v1.mk`（推荐）、`https://subapi.zrfme.com`、`https://subweb.7li7li.com`、`https://api.dler.io`。
均为第三方，**会发送订阅凭证**，公开后端也可能失效——隐私敏感请自建。

---

## 8. 旧脚本 → 新模块映射

| 原文件 | 新归属 |
| --- | --- |
| `update_sing_box_core.sh` | `core.py` |
| `update_and_resingbox.sh` | `flows/modify.py` 菜单项 4 |
| `download_sing_box_subscription.sh` | `subscription/convert.py`（subconverter 兜底分支）|
| `Enhance/clash_nodes_to_singbox.py` | `subscription/convert.py` + `customize.py`（`yaml`→`yamlmini`）|
| `Enhance/select_singbox_node.py` | `node_select.py` |
| `setup_sing_box_service.sh` | `service.py` + `templates/sing-box.service.tmpl` |
| `setup_resilience.sh` + `sing_box_healthcheck.sh` | `resilience.py` + `templates/` |
| `setup_weekly_update_timer.sh` | `timer.py` + `templates/update.timer.tmpl` |
| `clash_nodes_to_singbox_config.json` | `state/customize.json` |

---

## 9. 实施阶段计划

1. **骨架**：目录、`singbox.sh`、`paths.py`、`shell.py`、`menu.py`、`__main__.py`（菜单可跑通，子项打桩）。
2. **核心资源**：`core.py`（内核/UI/规则集下载）。
3. **订阅子系统**：`yamlmini`（先写 + 单测，拿真实 clash 订阅对比 PyYAML 输出）→ `fetch` + `detect` + `convert`（clash 路径移植旧 py，`yaml`→`yamlmini`）+ `manager`（命名/切换）。
4. **定制层**：`customize.py`（迁移 customize.json + apply）。
5. **服务**：`service.py` + systemd 模板，跑通初始化全流程。
6. **增强**：`node_select.py`、`resilience.py`、`timer.py`。
7. **base64 路径**：subconverter(target=clash) 调用 + 接入本地主转换器；应急本地解析(可选)。
8. **卸载流程** + README + 收尾。

---

## 10. 待定 / 未决问题

- base64 默认走云端 subconverter（已决）；本地解析仅作应急、默认关闭。
- subconverter 后端：默认填公开后端 `sub.v1.mk`，仅"填地址"（不内置自建引导，
  README 给自建 docker 指引即可）。
- `state/` 是否需要跨机器迁移/导出能力（订阅可重新拉取，配置可重生成，暂不做）。
- 多 active（同时跑多服务实例）暂不支持，保持单 active 模型。
