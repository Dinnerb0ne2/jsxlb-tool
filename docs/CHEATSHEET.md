# 快速参考 (Cheat Sheet)

命令速查 + 常见操作, 详细见对应文档。

## 顶层命令

| 命令 | 作用 |
|---|---|
| **双击 `start.bat`** | 一键开启 (提权): 定位→补丁→CA→hosts→代理→客户端 |
| **双击 `end.bat`** | 一键恢复 (提权): 停代理→关客户端→还原asar→删CA→清hosts |
| `py -3 scripts\hijack_daemon.py status` | 代理状态 + 最近改写事件 |
| `py -3 scripts\hijack_daemon.py reload` | 规则文件热更新 |
| `py -3 scripts\rules.py show` | 查看当前生效规则 |
| `py -3 scripts\rules.py add banner.replace "原文" "改文"` | 添加替换 (免手写 JSON/无转义) |
| `py -3 scripts\rules.py flag debug.dry_run on` | 预演模式 (只打印不改写) |
| `py -3 scripts\inject.py banner "文本" --sender 王老师` | 帧注入: 直接向大屏推横幅 (不经教师端) |
| `py -3 scripts\inject.py clients` | 在线会话 + 捕获概况 |
| `py -3 scripts\inject.py frames 50 --clear` | 看/清捕获环 (pass/rewrite/drop/inject) |
| `py -3 scripts\watch.py` | 事件流监听 (tail -f, 只看关键事件) |
| `py -3 scripts\schedule.py import timetable.json` | 导入课表, 三个弹窗按课表显示 |
| `py -3 scripts\schedule.py status` | 课表状态 (上课/课间/课表外) + 折叠队列 |
| `py -3 scripts\schedule.py force class` | 演练: 强制"上课中" |
| `py -3 scripts\netcheck.py` | 网络六步诊断 (hosts/公共DNS/系统DNS/DoH/出站/代理) |

## 看日志
```
logs/start_end.log         ★ start/end 流程日志 (失败先看这里)
logs/hijack_proxy.log      代理运行日志 (改写/丢帧/上游错误)
logs/hijack_patch.log      asar 补丁输出
logs/client_console.log    客户端日志 (launch_log.py 启动才有)
```

## 校园网 DNS 被封 (常见)

代理自带多层解析, **不需要手动配 DNS**。诊断:

```
py -3 scripts\netcheck.py
```

- [3] 系统/校内 DNS 有 IP → 自动可用, 无需干预
- [2][3][4] 全失败 → 在 `rules/upstream_ip.txt` 写一行真实 IP:
  ```
  8.134.221.255
  ```
  再双击 `start.bat`
- [5] 全 BLOCKED → 校园网封了出站 443, 与本工具无关, 换网络/热点

详见 [CAMPUS_NETWORK.md](CAMPUS_NETWORK.md)。

## 常用规则片段 (rules/hijack_rules.json)

```json
"banner": {
  "block_banner": false,
  "replace": [["原文", "改文"], ["1", "2"]],
  "remove": ["请家长签字"],
  "append": " 喵~",
  "force_sender": "",
  "force_tts": null
}
```

```json
"seat": {
  "exclude": ["张三"],
  "only": [],
  "pairs": [["小明", "小红"]]
}
```

```json
"timer": { "force_seconds": 0 },
"block_ws_types": [],
"passthrough": false
```

## 验证链路 (一条命令看一个环节)

```powershell
Resolve-DnsName xlb.810086.com                 # hosts 生效? 应 127.0.0.1
Test-NetConnection 127.0.0.1 -Port 443          # 代理活着?
py -3 scripts\netcheck.py                           # 全链路六步诊断
curl http://127.0.0.1:8100/__status                  # 在线会话 / 捕获数 / 上游 IP
curl "http://127.0.0.1:8100/__frames?n=20"            # 最近 20 条帧 (含动作标记)
# 代理日志出现以下行 = 各环节 OK
#   [ws] client connected          客户端进了代理
#   [ws] upstream: wss://.../ws    代理连上真实服务器
#   BANNER rewrite: 'a' -> 'b'     横幅改写生效
```

## 文档地图

| 需求 | 文档 |
|---|---|
| 完整攻击分析 (WP) | [WRITEUP.md](WRITEUP.md) |
| 配置怎么填 (详解) | [CONFIG_GUIDE.md](CONFIG_GUIDE.md) |
| 部署/日常/配置 | [USAGE.md](USAGE.md) |
| 技术原理 (为什么可行) | [PRINCIPLE.md](PRINCIPLE.md) |
| 规则字段全参考 | [RULES_REFERENCE.md](RULES_REFERENCE.md) |
| 故障排查 | [TROUBLESHOOTING.md](TROUBLESHOOTING.md) |
| 校园网专项 | [CAMPUS_NETWORK.md](CAMPUS_NETWORK.md) |
| 方法论与踩坑复盘 | [MISSION_REVIEW.md](MISSION_REVIEW.md) |
| 安全分析原始报告 | [../SECURITY_REPORT.md](../SECURITY_REPORT.md) |
