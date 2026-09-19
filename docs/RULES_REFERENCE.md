# 规则字段完整参考

规则文件: `rules/hijack_rules.json`, 生效方式见 USAGE.md 的"热更新规则"。

## 热更新行为 (重要)

- **POST /__rules 与 `reload` 都是深合并**: 只覆盖你提交的键, 同组其他键保留。
  例: 只提交 `{"banner": {"append": "喵~"}}` → 原 `replace/remove` 等不会丢。
- 想清空某字段: 显式提交空值, 如 `"replace": []`。
- 查看当前生效规则: `GET http://127.0.0.1:8100/__rules`。

## 顶层结构

```json
{
  "debug":    { ... },          // 调试 / 预演
  "banner":   { ... },          // 横幅改写
  "seat":     { ... },          // 座位/点名规则
  "timer":    { ... },          // 计时器
  "commands": { ... },          // 桌面控制命令拦截
  "files":    { ... },          // 文件下发拦截
  "block_ws_types": [],         // 封锁的消息类型
  "block_paths": [],            // 封锁的 API 路径
  "passthrough": false          // 总开关 (直通)
}
```

## banner (横幅)

| 字段 | 类型 | 说明 |
|---|---|---|
| `types` | `["text",...]` | 视为横幅的 messageType (默认 text/banner/notice; 支持尾部 `*` 前缀匹配如 `notice*`; 需要 popup 等自行加) |
| `block_banner` | bool | `true` = 丢弃一切横幅帧 (禁止横幅) |
| `replace` | `[["原文","改文"],...]` | 依序替换特定词 |
| `remove` | `["某话",...]` | 删除某些话 (直接删除子串) |
| `append` | string | 尾部追加固定文案 |
| `force_sender` | string | 改发件人显示名, `""` = 不改 |
| `force_tts` | bool/null | 强制开/关语音朗读, `null` = 不改 |

执行顺序: remove → replace → append。

改写作用于帧的 `content` 和 `metadata.content`, 所以 TTS 语音同步变化。

## debug (调试)

| 字段 | 类型 | 说明 |
|---|---|---|
| `log_frames` | bool | `true` = 记录每个下行帧的 type/messageType/id (排障) |
| `dump_dir` | string | 非空 = 把每个下行帧存成 json 到该目录 (抓包分析) |
| `dry_run` | bool | `true` = 只打印"将会改写什么", **不真的改** (预演) |
| `capture_max` | int | 内存捕获环条数 (默认 200, `0` = 关闭)。`GET /__frames` 查看; 动作标记 pass/rewrite/drop/up/inject |

用法建议: 先用 `dry_run` 确认规则命中, 再关掉正式生效。
未知类型的帧没生效时, 开 `log_frames` 看真实 messageType。

## commands (桌面控制命令)

| 字段 | 类型 | 说明 |
|---|---|---|
| `block` | `["lock_system",...]` | 拦截的命令名 (子串匹配) |
| `block_snapshot` | bool | `true` = 拦截摄像头抓拍 (`smart-attendance:snapshot`) |

可拦截的命令 (来自客户端源码): `lock_system`(锁屏) `shutdown_system`(关机)
`lock_app`/`unlock_app`(kiosk 锁) `launch_app` `smart-attendance:snapshot`(抓拍)
`desktop_update_force`(强更)。

## files (文件下发)

| 字段 | 类型 | 说明 |
|---|---|---|
| `block_traversal` | bool | 默认 `true`: 拦截含 `..` / 路径分隔符的文件名 (防路径穿越) |
| `block` | `["关键词",...]` | 拦截文件名含这些子串的下发 |

## seat (座位/随机点名)

| 字段 | 类型 | 说明 |
|---|---|---|
| `exclude` | `["姓名",...]` | 黑名单: 从座位表和点名名单移除 |
| `only` | `["姓名",...]` | 白名单: 只保留这些人 (空 = 全部) |
| `pairs` | `[["甲","乙"],...]` | 结对: 乙强制坐甲右邻, 原右邻与乙互换 |

作用路径:
- HTTP `/api/v2/seats/display/<classId>` 响应 (大屏座位表)
- HTTP `/api/v2/students?classId=` 响应 (托盘随机点名名单)
- WS `class.data.updated` / `class.data.snapshot` 帧内嵌的 seats/students

## timer (计时器)

| 字段 | 类型 | 说明 |
|---|---|---|
| `force_seconds` | int | `>0` 强制横幅 N 秒后自动关闭; `0` = 不干预 |

作用于帧的 `popup_duration` / `popupDuration` / `metadata.popup_duration`。

## 封锁

| 字段 | 类型 | 说明 |
|---|---|---|
| `block_ws_types` | `["type",...]` | 丢弃指定 `payload.type` 的 WS 帧 (默认空)。以 `*` 结尾做前缀匹配, 如 `desktop.update.*` |
| `block_paths` | `["/api/...",...]` | 命中前缀的 HTTP 请求返回 `{"success":true,"data":{}}` |

已知可封锁的 WS 类型 (来自 main.js handleGatewayPayload):

```
broadcast.message       横幅/指令/文件等一切广播 (慎用, 全封)
notification.push       通知推送
renderer.pack.push      渲染层热更新包 (汉化/界面替换通道)
desktop.update.*        自动更新
camera.signal           摄像头信号
class.todo.updated      班级待办
class.data.*            班级数据同步 (座位/学生/课表)
teacher.profile.updated 教师资料
```

## passthrough (总开关)

`true` = 直通模式。代理照常转发, 但所有改写/封锁规则全部跳过。
日志只记录连接事件。适合"想保持通道但暂不干预"的场景。

## schedule (课表调度 — 三个弹窗)

按导入的课表决定三个弹窗 (banner 横幅 / popup 小弹窗 / fullscreen 全屏) 何时显示。
状态: `class` 上课中 / `break` 课间 (含显式课间与课节空隙) / `off` 课表外 (放学、周末、放假)。

| 字段 | 说明 |
|---|---|
| `enabled` | 总开关; `false` 时连 `force` 也不生效 |
| `force` | `auto/class/break/off` — 演练用强制状态 |
| `weekdays` | 上课星期 (1=周一 … 7=周日) |
| `periods` | `[{"name":"第1节","start":"08:00","end":"08:45"}, ...]` (别名 begin/startTime、finish/endTime) |
| `breaks` | 显式课间, 空 = 课节空隙自动算课间 |
| `overrides` | `{"2026-10-01":"off"}` 放假 / `"school"` 补课 |
| `timetable_file` | 导入的课表文件; 出现同名键时覆盖内联值 |
| `blocked_action` | `fold` 课上收起、离开上课态自动补发 (默认) / `drop` 直接丢 |
| `fold_ttl_minutes` | 折叠队列保留时长 (默认 60) |
| `targets.banner/popup/fullscreen` | `allow` / `break_only` 课上压、课间与课表外放 / `class_only` 只在课上放 / `block` 全压 |

不参与调度: `command` / `file` 帧与 `class.data.*` 等非 `broadcast.message` 帧; `passthrough` 时同样失效。
完整填写说明、课表格式与配方 → [CONFIG_GUIDE.md](CONFIG_GUIDE.md)。

## 完整示例

```json
{
  "banner": {
    "block_banner": false,
    "replace": [["今晚交作业", "今晚自由活动"], ["请准时", "不用"]],
    "remove": ["请家长签字"],
    "append": " ——教务处",
    "force_sender": "",
    "force_tts": null
  },
  "seat": {
    "exclude": ["张三"],
    "only": [],
    "pairs": [["小明", "小红"], ["王五", "赵六"]]
  },
  "timer": { "force_seconds": 0 },
  "block_ws_types": ["renderer.pack.push"],
  "block_paths": [],
  "passthrough": false
}
```

效果: 横幅文本被改写、家长签字相关内容消失、尾部带教务处落款;
张三从座位表和点名消失; 小明小红、王五赵六永远同桌; 横幅时长随服务器;
热更新包被拦截; 其余功能不受影响。

## 注入与捕获 API (代理 HTTP 接口)

代理明文端口 (默认 8100, **默认只绑 127.0.0.1**; 需给同网段用则 `--admin-host 0.0.0.0`)
上除 `/__rules` 外还有:

| 接口 | 方法 | 说明 |
|---|---|---|
| `/__status` | GET | `clients`(在线会话) / `capture`(捕获条数) / `upstreamIp` / `passthrough` / `startedAt` |
| `/__clients` | GET | 在线教室会话数 |
| `/__frames` | GET | 捕获环: `?n=50` 取最近 50 条, `?clear=1` 取出并清空 |
| `/__inject` | POST | 帧注入 (见下) |

捕获条: `{t, dir, action, type, messageType, id, content/command..., raw(截断)}`。
`dir`: `down` 服务器→客户端 / `up` 客户端→服务器 / `inject` 本机注入。
`action`: `pass` 原样 / `rewrite` 被改写 / `drop` 被丢弃 / `up` 上行记录 / `inject` 注入。

`/__inject` 请求体三种写法:

```json
{"text": "文本", "sender": "王老师", "seconds": 60}
{"command": "lock_system"}
{"frame": {...}, "apply_rules": true}
```

也可带 `type` 字段直接发非广播帧 (如 `{"type": "renderer.pack.push", "version": 3}`)。
响应: `{"ok": true, "sent_to": N, "frame": {...}}`; 没有会话时 `sent_to` 为 0。
命令行封装: `scripts/inject.py` (banner/safety/teacher/command/raw/clients/frames)。

## 免手写 JSON: rules.py CLI

直接改 JSON 容易踩转义坑 (中文/引号/反斜杠)。用 CLI 更稳, 参数由 Python 处理编码:

```
py -3 scripts/rules.py show                          # 查看当前生效规则
py -3 scripts/rules.py get banner.append             # 读某键
py -3 scripts/rules.py set banner.append " 喵~"       # 设值 (自动识别 true/false/数字/JSON)
py -3 scripts/rules.py add banner.replace "原文" "改文"    # 数组追加
py -3 scripts/rules.py add seat.exclude "张三"
py -3 scripts/rules.py del banner.replace 0          # 删除数组元素 (索引)
py -3 scripts/rules.py rm banner.replace             # 清空该键
py -3 scripts/rules.py flag debug.dry_run on         # 布尔开关 (on/off)
py -3 scripts/rules.py reload                        # 写文件 + 热更新到代理
py -3 scripts/rules.py reset                         # 恢复默认模板 (旧文件备份 .bak)
```

注意: 纯数字会被当数字 (如 `add banner.replace 1 2` -> `[1,2]`);
要强制字符串就用 JSON 引号: `add banner.replace '"1"' '"2"'`。
代理侧对 replace/remove/append 已强制转字符串, 所以数字也能正常工作。
