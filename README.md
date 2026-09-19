# 教室小喇叭 AI 助手 — 中间人劫持工具链

对教室大屏客户端 (Electron) 与 810086.com 服务器之间的下行通信做透明劫持:
横幅改写、座位/点名规则操纵、计时器篡改、功能禁止。教师端与服务器均无感知。

**完整攻击分析 → [docs/WRITEUP.md](docs/WRITEUP.md)** (漏洞链、验证证据、缓解建议)

## 快速开始

```
双击 start.bat  一键开启: 定位客户端→asar补丁→CA信任→hosts劫持→静默代理→启动客户端 (自动提权)
双击 end.bat    一键关闭: 停代理→杀客户端→还原asar→删CA→清hosts (自动提权, 完全恢复)
```

客户端安装目录**自动定位** (注册表/常见路径/快捷方式/盘符扫描), 不依赖固定路径/版本号。
也可用 `XLB_CLIENT_DIR` 环境变量强制指定。

## 日常操作 (静默, 无窗口)

| 操作 | 命令 |
|---|---|
| 看状态 | `py -3 scripts\hijack_daemon.py status` |
| 改规则后热更新 | 编辑 `rules\hijack_rules.json` → `py -3 scripts\hijack_daemon.py reload` |
| 停代理 | `py -3 scripts\hijack_daemon.py stop` |
| 网络诊断 | `py -3 scripts\netcheck.py` (hosts/DNS污染/出站/代理 四步可验证) |
| 帧注入 (不经教师端) | `py -3 scripts\inject.py banner "文本" --sender 王老师` |
| 抓帧/回看 | `py -3 scripts\inject.py frames 50` (pass/rewrite/drop 动作标记) |
| 事件流监听 | `py -3 scripts\watch.py` (tail -f, 只看关键事件) |
| 课表调度 | `py -3 scripts\schedule.py status` / `import timetable.json` |

## 功能一览

| 功能 | 规则 (rules/hijack_rules.json) |
|---|---|
| 横幅文本替换/删除/尾部追加/改发件人 | `banner.replace[] / remove[] / append / force_sender` |
| 多组替换 (1→2, 3→4 …) | `banner.replace` 多对, 按序执行 |
| 禁止横幅 | `banner.block_banner: true` |
| 禁止热更新/汉化包下发 | `block_ws_types: ["renderer.pack.push"]` (默认空, 需要时加) |
| xx 和 xx 永远坐在一起 | `seat.pairs: [["小明","小红"]]` |
| 某人永不被随机点名 | `seat.exclude: ["张三"]` |
| 只抽指定的人 | `seat.only: ["李四"]` |
| 改横幅自动关闭时长 | `timer.force_seconds: 9999` |
| 封锁任意 API | `block_paths: ["/api/v2/xxx"]` |
| 代理在但不干预 (低调) | `passthrough: true` |
| 直推横幅/命令 (不经教师端) | `py -3 scripts\inject.py ...` 或 `POST /__inject` |
| 会话/帧捕获 (内存环) | `debug.capture_max` (默认 200) + `GET /__frames` |
| 三个弹窗按课表显示 | `schedule.targets` + `py -3 scripts\schedule.py import timetable.json` |
| 阻止客户端自动更新 (默认开启) | `update.block` 三层防线 (防 asar 补丁被更新覆盖) |

## 目录结构

```
jsxlb/                          ← 本工具链根 (整体拷贝可迁移)
├── README.md                   本文件
├── start.bat                   一键开启 (自动提权)
├── end.bat                     一键关闭/恢复 (自动提权)
├── src/                        核心库
│   ├── hijack_proxy.py         MITM 代理主程序 (规则引擎/防自环/抗污染)
│   ├── ctl_common.py           一键流程共享操作 (hosts/CA/进程/asar)
│   ├── client_locator.py       客户端自动定位 (5级策略)
│   └── patch_asar.py           asar 补丁 (等长字节替换, 毫秒级)
├── scripts/                    运维 / CLI 入口
│   ├── ctl_start.py / ctl_end.py   一键开启 / 恢复流程
│   ├── hijack_daemon.py        代理控制器 (start/stop/status/reload)
│   ├── rules.py                规则 CLI (免手写 JSON, 无转义烦恼)
│   ├── inject.py               帧注入 CLI (banner/safety/teacher/command/raw/frames)
│   ├── watch.py                事件流监听 (tail -f, 只看关键事件)
│   ├── schedule.py             课表调度 (import/status/check/force/on/off)
│   ├── netcheck.py             网络诊断 (六步, 含抗污染验证)
│   ├── run_client.py / launch_log.py   启动客户端
│   └── install_info.py         打印客户端路径
├── bin/                        一键脚本 (bat, 双击运行)
│   ├── hijack_on.bat / hijack_off.bat / hijack_patch.bat
│   ├── status.bat              状态一览 (含在线会话/捕获数)
│   ├── netcheck.bat / inject.bat   网络诊断 / 帧注入快捷入口
│   └── start_all_silent.bat / stop_proxy_silent.bat
├── testsuite/
│   └── test_inject.py          集成测试 (假上游 + 真代理 + 注入/捕获断言)
├── rules/
│   ├── hijack_rules.json       劫持规则 (运行中热更新)
│   └── upstream_ip.txt         上游真实 IP 手动兜底 (校园网 DNS 全封时用, 平时空)
├── docs/
│   ├── CHEATSHEET.md          ★ 命令速查 + 文档地图
│   ├── CONFIG_GUIDE.md        ★ 配置文件填写详解 (字段/课表调度/配方/常见错填)
│   ├── WRITEUP.md              ★ 攻击分析报告 (漏洞链/验证证据/缓解)
│   ├── USAGE.md                使用手册 (逐步)
│   ├── PRINCIPLE.md            技术原理
│   ├── RULES_REFERENCE.md      规则字段参考
│   ├── TROUBLESHOOTING.md      故障排查
│   ├── CAMPUS_NETWORK.md       校园网络专项 (DNS污染/出站封锁)
│   └── MISSION_REVIEW.md       方法论与踩坑复盘
├── backup/
│   ├── hijack_ca.pem           自签 CA (客户端信任根)
│   ├── hijack_cert.pem         服务器证书 (伪装 xlb.810086.com)
│   └── asar/app.asar           原版客户端存档 (灾难恢复)
└── logs/                       全部操作日志 (on/off/patch/proxy/pid)
```

## 文档导航

| 需求 | 文档 |
|---|---|
| 接手项目先看 | [docs/HANDOVER.md](docs/HANDOVER.md) |
| 命令速查 | [docs/CHEATSHEET.md](docs/CHEATSHEET.md) |
| 配置文件怎么填 (详解) | [docs/CONFIG_GUIDE.md](docs/CONFIG_GUIDE.md) |
| 怎么做到的 | [docs/PRINCIPLE.md](docs/PRINCIPLE.md) |
| 完整攻击分析 (WP) | [docs/WRITEUP.md](docs/WRITEUP.md) |
| 部署/日常/恢复 | [docs/USAGE.md](docs/USAGE.md) |
| 规则字段参考 | [docs/RULES_REFERENCE.md](docs/RULES_REFERENCE.md) |
| 故障排查 | [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) |
| 校园网专项 | [docs/CAMPUS_NETWORK.md](docs/CAMPUS_NETWORK.md) |
| 方法论复盘 | [docs/MISSION_REVIEW.md](docs/MISSION_REVIEW.md) |

## 安全边界

- 工具只作用于本机/授权网段, 所有系统改动有备份, `end.bat` 一键还原
- 代理对上行 (客户端→服务器) 零篡改, 服务器侧行为完全正常
- 完整恢复: 运行 `end.bat` 后系统回到劫持前状态 (asar 原版/hosts 干净/CA 删除/代理停止)
