# 配置填写指南 — rules/hijack_rules.json

> 目的: 拿到文件就能填对。每个字段写清 **填什么 / 影响什么 / 怎么验证**。
> 字段速查表在 [RULES_REFERENCE.md](RULES_REFERENCE.md); 本文回答"**怎么填、填了会怎样、怎么确认生效**"。
> 命令速查在 [CHEATSHEET.md](CHEATSHEET.md); 出问题看 [TROUBLESHOOTING.md](TROUBLESHOOTING.md)。

---

## 0. 一分钟上手

**文件位置**: `rules/hijack_rules.json` (UTF-8, 无 BOM)。整目录可迁移, 配置跟着目录走。

**三种改法**:

| 方法 | 命令 | 适用 |
|---|---|---|
| 编辑文件 + 热更新 | 改 `rules/hijack_rules.json` → `py -3 scripts\hijack_daemon.py reload` | 大改、加规则 |
| 规则 CLI (免手写 JSON) | `py -3 scripts\rules.py set banner.append " 喵~"` | 改单个值 (自动热更新) |
| HTTP 直接推 | `POST http://127.0.0.1:8100/__rules` (JSON body) | 脚本/程序化 |

**热更新是深合并**: 只提交要改的键, 同组其他键保留。例: 只发 `{"banner": {"append": " 喵~"}}`,
原 `replace/remove/force_sender` 不会丢。想清空某字段就显式提交空值 (`"replace": []`)。

**改配置的推荐流程**:

```
1. py -3 scripts\rules.py flag debug.dry_run on     # 预演: 只打印"将会改写什么", 不动数据
2. 触发一条横幅 / 一次点名, 看 logs/hijack_proxy.log
3. 命中符合预期 -> py -3 scripts\rules.py flag debug.dry_run off   # 正式生效
4. 没命中 -> py -3 scripts\rules.py flag debug.log_frames on, 看真实 messageType / type
```

**JSON 合法性自检** (填错语法时最有用):

```powershell
py -3 -c "import json;json.load(open(r'rules\hijack_rules.json',encoding='utf-8'));print('JSON OK')"
```

### 生效条件与边界

- 规则是**内存态**: 文件只在代理启动时读取, 改完必须 `reload` / `POST /__rules` 才生效。
- **代码升级后要重启代理** (`py -3 scripts\hijack_daemon.py restart`)。旧进程只认它启动时那一版代码,
  新字段 (如 `schedule`) 在旧进程里不存在, 怎么写都不会有反应。
- 字段名拼错**不会报错**, 只是静默不生效。写完先对照本文或 `rules.py show` 看一遍。
- 数值会被 `rules.py` 智能识别: `add banner.replace 1 2` 会变成数字 `[1,2]`;
  要字符串就带引号: `add banner.replace '"1"' '"2"'` (代理侧对 replace/remove/append 已强制转字符串, 数字也能用)。

---

## 1. 开箱默认 (完整)

```json
{
  "debug": { "log_frames": true, "dump_dir": "", "dry_run": false, "capture_max": 200 },
  "banner": {
    "types": ["text", "banner", "notice"],
    "block_banner": false,
    "replace": [],
    "remove": [],
    "append": "",
    "force_sender": "",
    "force_tts": null
  },
  "seat": { "exclude": [], "only": [], "pairs": [] },
  "timer": { "force_seconds": 0 },
  "commands": { "block": [], "block_snapshot": false },
  "files": { "block_traversal": true, "block": [] },
  "block_ws_types": [],
  "block_paths": [],
  "passthrough": false,
  "schedule": {
    "enabled": false,
    "force": "auto",
    "weekdays": [1, 2, 3, 4, 5],
    "periods": [],
    "breaks": [],
    "overrides": {},
    "timetable_file": "",
    "blocked_action": "fold",
    "fold_ttl_minutes": 60,
    "targets": { "banner": "break_only", "popup": "break_only", "fullscreen": "break_only" }
  }
}
```

默认状态: 代理只转发不改写, 调度关闭, 捕获环 200 条, 最详细日志 (逐帧) 开着。

---

## 2. 逐段详解

### 2.1 debug — 调试 / 预演 / 捕获

| 字段 | 填什么 | 影响 | 建议 |
|---|---|---|---|
| `log_frames` | `true/false` | 每个下行帧打一行 `帧[down] type=... messageType=... id=...` | 排障开; 日常可关 (帧多时刷屏) |
| `dump_dir` | 目录路径, `""`=关 | 每个下行帧存成 json 文件到该目录 | 抓包分析用, 例如 `"dump_dir": "tmp/frames"` |
| `dry_run` | `true/false` | `true` = 只打印"将会: 横幅改写 …", **不改任何数据** | 上规则前先跑一遍 |
| `capture_max` | 数字, `0`=关 | 内存捕获环条数, `GET /__frames` 可查 | 默认 200 够用 |

捕获环里每条都带动作标记: `pass` 原样 / `rewrite` 被改写 / `drop` 被丢弃 /
`fold` 课上收起 / `release` 课间补发 / `up` 上行记录 / `inject` 本机注入。

### 2.2 banner — 横幅改写

| 字段 | 填什么 | 说明 |
|---|---|---|
| `types` | `["text","banner","notice"]` | 视为横幅的 `messageType`。支持尾部 `*` 前缀匹配, 如 `["notice*"]` 可覆盖 `notice.popup`。不知道真实类型时开 `log_frames` 看 |
| `block_banner` | `true/false` | `true` = 横幅帧直接丢弃, 大屏不弹 |
| `replace` | `[["原文","改文"], ...]` | 按数组顺序逐个替换, 可多组 (1→2, 3→4) |
| `remove` | `["某句话", ...]` | 直接删掉子串 |
| `append` | 字符串 | 尾部追加, 传统用法 `" 喵~"` |
| `force_sender` | 字符串, `""`=不改 | 改发件人显示名 |
| `force_tts` | `true/false/null` | 强制开/关语音朗读, `null`=不改 |

- **执行顺序固定**: `remove` → `replace` → `append`。写规则时按这个顺序推演结果。
- 改写同时作用于帧的 `content` 与 `metadata.content`, 所以**语音朗读 (TTS) 与横幅一致变化**。
- `replace` 的查找是普通子串匹配, 不是正则。
- 例: 原文 `今晚交作业,请家长签字`, 配 `remove:["请家长签字"]` + `replace:[["今晚交作业","今晚自由活动"]]` + `append:" ——教务处"` →
  大屏显示 `今晚自由活动 ——教务处`。

### 2.3 seat — 座位 / 随机点名

| 字段 | 填什么 | 说明 |
|---|---|---|
| `exclude` | `["张三", ...]` | 黑名单: 座位表里标空、点名名单里删掉 (永不被抽) |
| `only` | `["李四", ...]` | 白名单: 只留这些人 (空数组 = 全部保留) |
| `pairs` | `[["小明","小红"], ...]` | 结对: 小红强制坐小明右邻位, 原右邻与小红互换 |

- 名字必须与服务器数据**完全一致** (空格、全角半角都算数)。不确定时先 `debug.log_frames` 或看座位表原始数据。
- 生效三条路径: 座位表 HTTP (`/api/v2/seats/...`)、随机点名 HTTP (`/api/v2/students`)、
  班级数据推送帧 (`class.data.*` 内嵌 seats/students)。
- 客户端有本地缓存 (约 180 天): 改完规则若大屏没变, 让教师端动一次座位/学生, 或重启客户端。
- 验证日志: `SEAT pair: 甲 <-> 乙 now adjacent` / `SEAT exclude: {'丁'} -> empty` / `STUDENTS rewritten`。

### 2.4 timer — 计时器

| 字段 | 填什么 | 说明 |
|---|---|---|
| `force_seconds` | 数字, `0`=不干预 | `>0` 时强制横幅/文件弹窗在 N 秒后自动关闭 |

作用字段: `popup_duration` / `popupDuration` / `metadata.popup_duration`。验证日志 `TIMER forced: Ns`。

### 2.5 commands — 桌面控制命令拦截

| 字段 | 填什么 | 说明 |
|---|---|---|
| `block` | `["lock_system", ...]` | 命中的命令帧直接丢弃 (子串匹配, 大小写不敏感) |
| `block_snapshot` | `true/false` | `true` = 拦截摄像头抓拍 (`smart-attendance:snapshot`) |

可用命令名 (取自客户端源码): `lock_system` 锁屏 / `shutdown_system` 关机 /
`lock_app`、`unlock_app` kiosk 锁 / `launch_app` / `smart-attendance:snapshot` 抓拍 /
`desktop_update_force` 强制更新。验证日志: `BLOCK command=lock_system (命令拦截)`。

### 2.6 files — 文件下发拦截

| 字段 | 填什么 | 说明 |
|---|---|---|
| `block_traversal` | `true/false` (默认 `true`) | 拦截文件名含 `..` 的路径穿越下发 |
| `block` | `["关键词", ...]` | 文件名含这些子串就丢帧 |

### 2.7 block_ws_types / block_paths — 通用封锁

| 字段 | 填什么 | 说明 |
|---|---|---|
| `block_ws_types` | `["renderer.pack.push", "desktop.update.*"]` | 丢弃指定 `payload.type` 的 WS 帧; 尾部 `*` 前缀匹配 |
| `block_paths` | `["/api/v2/xxx"]` | 命中前缀的 HTTP 请求返回假成功 `{"success":true,"data":{}}` |

已知可封锁的 WS 类型 (来自客户端 `handleGatewayPayload`):

```
notification.push       通知推送
renderer.pack.push      渲染层热更新包 (汉化/界面替换通道)
desktop.update.*        自动更新
camera.signal           摄像头信令
class.todo.updated      班级待办
class.data.*            班级数据同步 (座位/学生/课表)   <- 封了座位规则就没数据可改
teacher.profile.updated 教师资料
broadcast.message       一切横幅/指令/文件广播         <- 慎用, 全封等于静音
```

### 2.8 passthrough — 总开关

`true` = 直通模式: 代理照常转发, **所有改写/封锁/调度全部跳过** (捕获环仍在记录, 注入仍可用)。
适合"通道保持但暂不干预", 也是规则排查的对照实验: 打开后问题消失 = 问题出在规则上。

### 2.9 schedule — 课表调度 (三个弹窗按课表显示)

**能调度谁**: 三个弹窗由帧的 `displayMode` 决定 (客户端 `main.js:12472-12497`):

| 目标 | 对应 displayMode | 实际界面 |
|---|---|---|
| `banner` | 缺省 / `banner` | 横幅 |
| `popup` | `popup` | 小弹窗 |
| `fullscreen` | `fullscreen` / `daily_safety_fullscreen` / `head_teacher_message_fullscreen` | 全屏 (含每日安全、班主任寄语) |

**状态口径**:

| 状态 | 判定 |
|---|---|
| `class` 上课中 | 落在任一课节区间 `[start, end)` |
| `break` 课间 | 落在显式 `breaks` 里, 或落在两个课节之间的空隙 (自动) |
| `off` 课表外 | 首节课之前 / 末节课之后 / 非上课星期 / 放假 / 课表为空 |

**字段**:

| 字段 | 填什么 | 说明 |
|---|---|---|
| `enabled` | `true/false` | **总开关**。`false` 时下面所有字段都不生效, 连 `force` 也无效 |
| `force` | `auto/class/break/off` | 演练用: 强制状态, 优先于课表与日期。演示"课上效果"不用等真课表 |
| `weekdays` | `[1,2,3,4,5]` | 上课的星期 (1=周一 … 7=周日) |
| `periods` | `[{"name":"第1节","start":"08:00","end":"08:45"}, ...]` | 课节; 字段别名 `begin`/`startTime`、`finish`/`endTime` 都认 |
| `breaks` | `[{"name":"大课间","start":"09:40","end":"10:10"}]` | 显式课间, 可空; 空 = 课节空隙自动算课间 |
| `overrides` | `{"2026-10-01":"off","2026-09-26":"school"}` | 日期覆盖: `off/holiday/rest` 放假, `school/work` 补课 |
| `timetable_file` | 文件路径, `""`=用内联 `periods` | 导入的课表 (见第 3 节); 文件里出现的键会覆盖同名的内联值 |
| `blocked_action` | `fold` / `drop` | 被压下时: `fold`=先收起, 离开上课态自动补发; `drop`=直接丢 |
| `fold_ttl_minutes` | 数字 (默认 60) | 折叠队列最长保留时长, 过期丢弃 |
| `targets.banner` / `targets.popup` / `targets.fullscreen` | 策略, 见下表 | 每个弹窗独立策略 |

**四种策略**:

| 策略 | 上课中 (`class`) | 课间 (`break`) | 课表外 (`off`) |
|---|---|---|---|
| `allow` (或写错/缺省) | 放行 | 放行 | 放行 |
| `break_only` | **压下** | 放行 | 放行 |
| `class_only` | 放行 | 压下 | 压下 |
| `block` | 压下 | 压下 | 压下 |

**fold 与 drop 怎么选**:

- `fold` (默认): 课上到的横幅先"收起"进内存队列, 进入课间/课表外的那一刻按到达顺序**自动补发**,
  一条不丢。队列上限 50 条、过期时间 `fold_ttl_minutes`, 代理重启即清空 (内存态)。
- `drop`: 课上直接丢, 老师发过的东西就没了。适合"只要课间安静, 内容无所谓"的场景。

**不参与调度的帧** (永远放行): `command` 命令帧、`file` 文件帧、`class.data.*` 等数据/信令帧,
以及一切非 `broadcast.message` 的帧。`passthrough: true` 时调度同样不生效。
`/__inject` 直接注入 (不带 `apply_rules`) 也不受调度影响 —— 操作者显式指令优先。

**验证**:

```
py -3 scripts\schedule.py status                  # 当前状态 + 折叠队列条数
py -3 scripts\schedule.py force class             # 演练: 强制上课中
curl -s http://127.0.0.1:8100/__status            # schedule / foldQueue 字段
tail 日志关键字: SCHED fold / SCHED release / SCHED drop
```

---

## 3. 课表导入与维护

课表就是一个独立 JSON (`rules/timetable.json`), 可以手写, 也可以从学校系统导出后改字段名 ——
导入时只认 `name/start/end` (别名见上), 其余字段原样保留。

**格式**:

```json
{
  "weekdays": [1, 2, 3, 4, 5],
  "periods": [
    { "name": "第1节", "start": "08:00", "end": "08:45" },
    { "name": "第2节", "start": "09:00", "end": "09:45" },
    { "name": "第3节", "start": "10:00", "end": "10:45" }
  ],
  "breaks": [],
  "overrides": { "2026-10-01": "off" }
}
```

**校验规则**: `end` 必须晚于 `start` (不支持跨午夜); 解析不了的课节**整条跳过**并不报错,
所以导入后一定要看一眼 `schedule.py periods` 的输出条数对不对。

**命令**:

```powershell
py -3 scripts\schedule.py import timetable.json     # 导入 -> rules/timetable.json + 开启调度
py -3 scripts\schedule.py periods                   # 看解析结果 (课节 + 自动课间)
py -3 scripts\schedule.py check 08:30 --date 2026-09-21   # 离线推算某时刻状态
py -3 scripts\schedule.py status                    # 实时状态 (问代理, 带折叠队列)
py -3 scripts\schedule.py force class               # 演练: 强制"上课中"
py -3 scripts\schedule.py off / on                  # 临时关/开调度 (课表保留)
```

**维护**: `rules/timetable.json` 改完直接存盘即可 —— 代理按文件 **mtime 自动重载** (无需 reload),
日志会出现 `[schedule] timetable loaded from ...`。放假长名单写在 `overrides`, 不用动 `weekdays`。

---

## 4. 常用配方

**① 课间显示、课上收起 (最常用)**

```json
"schedule": {
  "enabled": true,
  "timetable_file": "rules/timetable.json",
  "blocked_action": "fold",
  "targets": { "banner": "break_only", "popup": "break_only", "fullscreen": "break_only" }
}
```

效果: 上课中三个弹窗一律不出; 课间与放学后正常显示; 课上错过的横幅在打下课铃后自动补发。

**② 上课只禁全屏, 横幅照常**

```json
"schedule": {
  "enabled": true, "timetable_file": "rules/timetable.json",
  "targets": { "banner": "allow", "popup": "allow", "fullscreen": "break_only" }
}
```

**③ 考试周全禁 (临时, 不动课表)**

```json
"banner": { "block_banner": true },
"timer": { "force_seconds": 30 }
```

**④ 座位小动作**

```json
"seat": { "exclude": ["张三"], "only": [], "pairs": [["小明", "小红"]] }
```

**⑤ 低调值守** (通道全开, 只记录不改)

```json
"passthrough": true, "debug": { "capture_max": 500 }
```

之后 `py -3 scripts\inject.py frames 100` 可以复盘最近发生了什么。

**⑥ 横幅加料 + 换发件人**

```json
"banner": {
  "replace": [["今晚交作业", "今晚自由活动"]],
  "remove": ["请家长签字"],
  "append": " 喵~",
  "force_sender": "教务处",
  "force_tts": true
}
```

---

## 5. 常见填错

| 现象 | 原因 | 处理 |
|---|---|---|
| 改完毫无反应 | 没热更新 | `rules.py reload` 或 `hijack_daemon.py reload` |
| 新字段 (如 `schedule`) 无反应 | 代理是旧代码启动的 | `py -3 scripts\hijack_daemon.py restart` |
| 有 `BANNER rewrite` 但大屏没变 | 弹窗被课表压下了 / `block_banner` 开着 | `schedule.py status`; 看 `SCHED fold/drop` 日志 |
| 座位规则不生效 | 名字不一致 / 客户端缓存 | 抄原始数据里的名字; 让教师端动一次座位或重启客户端 |
| JSON 存盘后规则全丢 | 语法错误导致加载失败 | 用第 0 节的 `json.load` 自检命令 |
| `replace` 没用 | 顺序理解反了 (remove 先跑) / 原文已被前面的替换改掉 | 按 remove→replace→append 推演 |
| 数字替换报错/变数字 | `rules.py` 把 `1` 当数字存了 | 代理侧已强制转字符串, 一般无碍; 也可 `rules.py add banner.replace '"1"' '"2"'` |
| 课表导入后状态全是"课表外" | `periods` 没解析出来 (格式/end<start) | `schedule.py periods` 看条目, 修正后重新 import |
| `force` 不生效 | `schedule.enabled` 是 `false` | 先 `schedule.py on` |

---

## 6. 完整示例 (一份填满的配置)

```json
{
  "debug": { "log_frames": false, "dump_dir": "", "dry_run": false, "capture_max": 200 },
  "banner": {
    "types": ["text", "banner", "notice", "popup*"],
    "block_banner": false,
    "replace": [["今晚交作业", "今晚自由活动"], ["请准时", "不必了"]],
    "remove": ["请家长签字"],
    "append": " ——教务处",
    "force_sender": "教务处",
    "force_tts": null
  },
  "seat": { "exclude": ["张三"], "only": [], "pairs": [["小明", "小红"]] },
  "timer": { "force_seconds": 0 },
  "commands": { "block": ["shutdown_system"], "block_snapshot": true },
  "files": { "block_traversal": true, "block": [] },
  "block_ws_types": ["renderer.pack.push"],
  "block_paths": [],
  "passthrough": false,
  "schedule": {
    "enabled": true,
    "force": "auto",
    "weekdays": [1, 2, 3, 4, 5],
    "periods": [],
    "breaks": [],
    "overrides": { "2026-10-01": "off" },
    "timetable_file": "rules/timetable.json",
    "blocked_action": "fold",
    "fold_ttl_minutes": 60,
    "targets": { "banner": "break_only", "popup": "break_only", "fullscreen": "break_only" }
  }
}
```

等价的口令版 (免手写 JSON):

```powershell
py -3 scripts\rules.py add banner.replace "今晚交作业" "今晚自由活动"
py -3 scripts\rules.py add banner.remove "请家长签字"
py -3 scripts\rules.py set banner.append " ——教务处"
py -3 scripts\rules.py add seat.exclude "张三"
py -3 scripts\rules.py add seat.pairs "小明" "小红"
py -3 scripts\rules.py add commands.block shutdown_system
py -3 scripts\rules.py flag commands.block_snapshot on
py -3 scripts\rules.py add block_ws_types renderer.pack.push
py -3 scripts\schedule.py import timetable.json
py -3 scripts\rules.py reload
```

---

## 7. 验证清单与回滚

**看效果, 一条链路一个关键字** (`logs/hijack_proxy.log`):

```
[ws] client connected            客户端进了代理
[schedule] timetable loaded      课表被加载/更新
BANNER rewrite: 'a' -> 'b'       横幅被改写 (含完整内容)
BLOCK command=lock_system        命令被拦
frame DROPPED                    帧被丢弃
SCHED fold / SCHED release       课上收起 / 课间补发
TIMER forced: Ns                 计时器被强改
SEAT pair / SEAT exclude         座位规则执行
STUDENTS rewritten               点名名单改写
INJECT -> N client(s)            帧注入下发
```

**接口自检**:

```powershell
curl -s http://127.0.0.1:8100/__rules        # 当前生效规则
curl -s http://127.0.0.1:8100/__status       # 会话数/捕获数/课表状态/折叠队列
curl -s "http://127.0.0.1:8100/__frames?n=20"   # 最近 20 条帧 (带动作标记)
```

**回滚**:

| 想退回什么 | 怎么做 |
|---|---|
| 只暂停干预 | `passthrough: true` + reload |
| 恢复默认规则 | `py -3 scripts\rules.py reset` (旧文件备份为 `hijack_rules.json.bak`) |
| 关掉课表调度 | `py -3 scripts\schedule.py off` (课表文件保留) |
| 一切恢复原状 | 双击 `end.bat` |
