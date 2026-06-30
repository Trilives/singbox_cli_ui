# sing-box CLI 部署系统

在 Linux 上交互式部署 / 管理 sing-box 的命令行系统。一个入口 `./singbox.sh`，
全流程交互完成：**初始化 / 更改配置 / 卸载**。

- **零第三方依赖**：只用系统自带 `python3` 标准库，不装 pip 包、不用虚拟环境
  （部署机常无代理，装包慢/易失败）。Clash YAML 用内置 `yamlmini` 解析（替代 PyYAML）。
- **自绘 TUI**：方向键导航、反显高亮、边框盒子；非 TTY 自动回退编号菜单。
- **随时可中止可回退**：任意步骤按 **ESC** 取消，已应用的改动自动回滚。
- **按需提权**：普通用户启动，需要 root 时自动 `sudo`（也可 `sudo ./singbox.sh`）。

设计细节见 [DESIGN.md](DESIGN.md)。

## 界面预览

终端为 TTY 时进入自绘 TUI：方向键移动、`⏎` 确认、`esc` 取消/返回；
选中行实际为**青色加粗高亮**（下图以 `❯` 光标示意）。

主菜单：

```
┌─ sing-box 部署系统 ────────────┐
│                                │
│  ❯ ① 初始化（首次部署）        │
│    ② 更改配置                  │
│    ③ 卸载所有服务              │
│                                │
│  ↑/↓ 选择   ⏎ 确认   esc 退出  │
└────────────────────────────────┘
```

「更改配置」子菜单：

```
┌─ 更改配置 ─────────────────────┐
│                                │
│    ① 订阅管理                  │
│  ❯ ② 切换 / 测速节点           │
│    ③ 编辑定制层                │
│    ④ 更新内核 / UI / 规则集    │
│    ⑤ 服务设置                  │
│    ⑥ 网络自愈                  │
│    ⑦ 每周自动更新              │
│                                │
│  ↑/↓ 选择   ⏎ 确认   esc 返回  │
└────────────────────────────────┘
```

卸载为多选清单（`空格` 勾选）：

```
┌─ 选择要卸载的项目 ─────────────────────────┐
│                                            │
│  [x] sing-box 服务                         │
│  [ ] 网络自愈钩子                          │
│  [x] 每周更新定时器                        │
│  [ ] 已下载产物                            │
│  [ ] 全部状态（订阅/配置）                 │
│                                            │
│  ↑/↓ 移动   空格 勾选   ⏎ 确认   esc 取消  │
└────────────────────────────────────────────┘
```

文本输入与确认带 `❯` 提示符；非 TTY（管道/重定向/CI）自动回退为编号菜单：

```
❯ 局域网下载代理（留空跳过）: http://192.168.1.2:7890
❯ 是否启用增强配置? [y/N]: y
```

## 快速开始

```bash
chmod +x singbox.sh
./singbox.sh
```

进入主菜单后选择「初始化」，按提示完成：

1. 填局域网下载代理（可留空；用于加速下载内核/UI/规则集）。
2. 下载 sing-box 内核 + Web UI + CN 规则集。
3. （可选）启用增强配置：地区分组（新加坡 / 香港）、AI·流媒体分流等。
4. 添加首个订阅。
5. 注册 systemd 服务，（可选）网络自愈、每周更新。

## 订阅来源（三选一）

| 来源 | 说明 |
| --- | --- |
| **Clash 订阅**（★推荐） | YAML，本地转换、不外泄凭证、兼容性最好 |
| **sing-box 直链** | 机场直接提供的 sing-box 配置，可选注入定制层 |
| **通用 base64** | 默认经云端 subconverter 解析为 Clash 再本地转换 |

订阅本地命名保存于 `state/subscriptions/<name>/`，可随时**切换生效订阅**。

> 隐私：base64 走第三方 subconverter 会发送节点凭证。默认后端 `https://sub.v1.mk`，
> 隐私敏感者可在「定制层」改为自建后端（docker 一条命令）。

## 定制层

「更改配置 → 编辑定制层」交互式增删改：AI/流媒体/直连域名、TUN 排除网段/UID、
直连进程、引导 DNS、地区组关键词、LAN 面板、**局域网代理**（放开 7890 给其他主机用，
开关时按需更新防火墙）、subconverter 后端、GitHub 加速、下载代理。
持久化于 `state/customize.json`。编辑器为**缓冲式**：「保存并退出」才写盘，ESC 放弃本次修改。

> 「更改配置」整个会话也是一个事务：期间的配置改动均为临时，选「💾 保存并退出」才提交，
> 在主菜单按 ESC 则回退本次会话的全部配置改动。

## 命令行（非交互，便于脚本/定时器）

```bash
./singbox.sh init        # 初始化
./singbox.sh modify      # 更改配置
./singbox.sh uninstall   # 卸载
./singbox.sh update      # 更新内核/UI/规则集并同步重启（每周定时器调用）

# 单模块调用
python3 -m singbox_deploy.core --only ruleset --force
python3 -m singbox_deploy.service status -n sing-box
```

## Web UI

面板默认仅本机 `http://127.0.0.1:9090/ui`。远程查看用 SSH 端口转发：

```bash
ssh -N -L 9090:127.0.0.1:9090 user@server
```

确需开放局域网时在「定制层」开启 `lan_panel`（务必设 secret + 防火墙）。

## 目录结构

```
Singbox/
├── singbox.sh              # 瘦入口：环境检查 → 调起 Python CLI
├── lib/singbox_deploy/    # Python 主体（零依赖，模块可单独 -m 调用）
├── templates/             # systemd unit / NM 钩子 / healthcheck 模板
├── tests/                 # yamlmini / 转换器 对拍测试（需 PyYAML 仅测试用）
└── state/                 # 运行期产物（gitignore：内核/UI/规则集/订阅/配置）
```

## 测试

```bash
python3 tests/test_yamlmini.py    # YAML 解析对拍 PyYAML
python3 tests/test_convert.py     # 转换器对拍参考实现 + sing-box check
```

## 环境要求

Linux + systemd；系统自带 `python3`（≥3.8）、`curl`、`tar`。TUN/服务需 root（自动 sudo）。

## 第三方资产与致谢

本项目**不打包任何二进制/UI/规则集**，全部在运行时从上游按需下载（见 `core.py`），
各自保留其原始许可证，归属各自作者：

| 资产 | 来源 | 用途 |
| --- | --- | --- |
| sing-box 内核 | [SagerNet/sing-box](https://github.com/SagerNet/sing-box)（GPL-3.0） | 代理核心 |
| Web 面板 | [MetaCubeX/metacubexd](https://github.com/MetaCubeX/metacubexd) | Clash API 面板 |
| CN 规则集 | [SagerNet/sing-geosite](https://github.com/SagerNet/sing-geosite)、[sing-geoip](https://github.com/SagerNet/sing-geoip) | 国内分流 |
| 订阅转换后端 | 公共 [subconverter](https://github.com/asdlokj1qpi23/subconverter) 实例（默认 `sub.v1.mk`） | base64 来源解析 |

> base64 来源默认经第三方 subconverter 解析，会向其发送订阅凭证；隐私敏感者请在
> 「定制层」改用自建后端。详见 [DESIGN.md](DESIGN.md) 的隐私权衡说明。

## 许可证

本项目代码以 [MIT](LICENSE) 许可证发布。上述第三方资产不随本仓库分发，使用时受其各自许可证约束。
