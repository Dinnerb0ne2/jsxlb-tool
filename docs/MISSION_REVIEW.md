# 任务复盘 — 教室小喇叭中间人劫持: 方法论与完整路径

> 目标: 劫持教室大屏客户端 (Electron) 与 810086.com 服务器之间的通信, 实现横幅改写、
> 座位规则、计时器控制、功能禁止。全程透明, 教师端无感知。

> **文档时效说明**: 本文是过程复盘 (写于工具链早期, 当时入口是 `bin/hijack_on.bat` /
> `bin/hijack_off.bat`)。最终交付的命令入口已统一为根目录 `start.bat` / `end.bat`
> (内部由 ctl_start.py / ctl_end.py 驱动, 能力等价且日志更细), 本文涉及的旧文件名
> 在 bin/ 中仍保留为手动等价入口。命令对照见 USAGE.md。

## 一、方法论 (为什么这样做)

### 1.1 先审计, 后动手

一切从 `SECURITY_REPORT.md` 的源码审计出发 (15512 行 main.js 逐段看),
拿到的是**行号级证据**, 不是猜测:

```
main.js:1669   服务器地址可被环境变量/解析重定向 (无钉扎)
main.js:204/656 NODE_TLS_REJECT_UNAUTHORIZED='0' (全局 TLS 放宽)
main.js:6670   WS 网关显式 rejectUnauthorized: true  ← 报告没写, 后面踩坑在这
main.js:6478   handleGatewayPayload 消息分发 (无来源校验)
main.js:6127   getPopupDurationSeconds (计时器读取点)
main.js:10518  /api/v2/seats/display/<classId> (座位数据入口)
main.js:10245  /api/v2/students (随机点名学生名单)
banner.html:587 content 渲染点 (横幅文本)
```

**教训**: 报告的 TLS-1 结论只对了一半。`NODE_TLS_REJECT_UNAUTHORIZED='0'`
只影响**默认**校验; 显式 `rejectUnauthorized: true` 的连接不受影响。
这个半结论浪费了我们一轮"未连接服务器"的排障。

### 1.2 中间人位置选择

教师手机 → 服务器 → 教室大屏。改写点选在**服务器→教室端**的下行链路上:

```
教师手机 ──► 真实服务器 ──► [代理: 伪装服务器+规则引擎] ──► 教室大屏
                              ↑ 改这里: 下行帧过规则, 上行透传
```

选代理而不是改客户端渲染, 因为:
- 一处改写覆盖所有下行数据 (横幅/座位/计时器/通知)
- 客户端二进制不用大动 (只放宽证书校验)
- 真实服务器视角一切正常 (上行原样), 教师端无感知

### 1.3 每一步都要有 oracle (可验证判据)

不信任"应该可以", 每个环节都造一个能跑的验证:

| 环节 | oracle |
|---|---|
| hosts 生效 | `socket.gethostbyname('xlb.810086.com') == 127.0.0.1` |
| 代理活着 | `TCP 127.0.0.1:443 可连` + netstat LISTENING |
| 上游穿透 | 经代理请求 → 官方 401 `DEVICE_REGISTRATION_AUTH_MISSING` (真实应答) |
| WS 通道 | 日志 `client connected` → `upstream: wss://...` → ESTABLISHED 持续 |
| 证书补丁 | node 手动测 `rejectUnauthorized:true + NODE_EXTRA_CA_CERTS` → 200 OK |
| 横幅改写 | 日志 `BANNER rewrite: '你好这是一次测试' -> '这不是测试'` + 大屏实测 |
| 二维码 API | 经代理 POST → 200 + 363KB JSON 解析 OK |
| 头像 URL | 经代理 GET → 5117 字节, JPEG magic `\xff\xd8\xff` |

### 1.4 失败即回滚, 系统永远可恢复

所有系统级改动 (hosts / CA 存储 / app.asar) 都有备份, `hijack_off.bat` 一键还原。
实际验证过完整 off→on 循环。

## 二、踩过的坑 (真实排障记录)

### 坑 1: MITM 自环 (最隐蔽)

hosts 把 `xlb.810086.com → 127.0.0.1` 后, **代理自己连上游也被 hosts 骗回自己**。
死循环, 全部超时, 客户端"未连接"。
**修复**: 代理启动时用原始 UDP DNS 包 (223.5.5.5) 绕过 hosts 拿真实 IP, 固定直连。

### 坑 2: 自签证书被显式校验拒绝

自签证书 → WS 网关握手失败 → 186 次重连全败 → "未连接服务器"。
**演进过程**:
1. 先试自签证书 (失败, 上面原因)
2. 改 CA 签发证书 + `NODE_EXTRA_CA_CERTS` 注入信任 (node 单测通过, 但 Electron 进程内 env 注入不可靠)
3. **终解**: 解包 asar, 8 处 `rejectUnauthorized: true → false`, 重打包 (备份保留)

### 坑 3: npx asar 工具的空格路径 bug

`C:\Program Files\jsxlb\...` 带空格, npx-asar 内部引号解析错误。
**修复**: 复制到项目内无空格临时目录 (patch_asar.py 的 tmp/) 操作, 完成后回拷。
(历史版本用 C:/xlb_tmp, 现已改为项目内, 消除 C 盘依赖)
后来整个补丁流程改写为 `patch_asar.py` (Python 驱动), 不再依赖 bat+npx 的脆弱组合。

### 坑 4: Program Files 的只读 + UAC

回写 asar 需要管理员 + 去只读属性。提权窗口 (Start-Process -Verb RunAs) 在
无人值守下没人点"是"会卡死。**修复**: 回拷逻辑放进 patch_asar.py, 由管理员
运行的 bat 统一提权一次; 用完成信号文件 (cb_done.txt) 轮询确认。

### 坑 5: off 之后 on 误判"已补丁"

`hijack_off.bat` 还原 asar 后 `.bak` 仍在, `hijack_on.bat` 用"备份存在"判断
补丁状态 → 误跳过 → 客户端又是原版 → 全套症状复发 (乱码/扫码失败/登录坏)。
**修复**: 判断改为**大小对比**: asar 与 .bak 同大 = 被还原需重打; 不同 = 已补丁。

### 坑 6: 端口绑定竞态 (10048)

restart 时旧实例 socket 未完全释放, 新实例 bind 失败但 daemon 报"启动成功"。
**修复**: daemon 的 start/restart 前先 `wait_ports_free()` (探测 8100/443 可连=占用, 循环等)。

### 坑 7: elevated 进程管理

管理员启动的代理进程, 普通权限下 Get-CimInstance 拿不到命令行 → daemon 误判
"不是我们的进程" → 状态错乱杀不掉。
**修复**: 双重验证 — 命令行含 `hijack_proxy.py` **或** PID 监听 443/8100。

### 坑 8: 字符串被当字符序列迭代

`dns_resolve` 返回 IP 字符串, `for ip in dns_resolve(...)` 逐字符迭代,
`all_ips` 变成 `['8','.','1','3',...]`, 探活全废, 日志打出 `upstream IP: 8`。
**修复**: `ip = dns_resolve(...)` 直接拿字符串。

### 坑 9: 运行中改规则文件不生效

规则是**内存态**, 文件只在启动时加载。
**修复**: `POST /__rules` 热更新 + 深合并 (部分提交不丢同组其他键) +
`hijack_daemon.py reload` 一键推规则文件。

### 坑 10: 控制台乱码是显示问题不是数据问题

GBK 控制台打印 UTF-8 中文全乱, 但**逻辑正确** (selftest 断言全过)。
判断数据正确性靠断言和字节对比, 不靠肉眼看日志。"瑙ｆ瀽鍝嶅簲澶辫触"这类
乱码甚至存在于客户端源码本身 (开发者双重编码)。

## 三、最终架构

```
jsxlb/
├── bin/
│   ├── hijack_proxy.py     代理主程序:
│   │     - 防自环 (raw DNS 解析真实 IP)
│   │     - 多 DNS 交叉 + TCP 探活选路 (校园网容错)
│   │     - WS 双向泵: 下行帧过规则引擎, 上行透传
│   │     - HTTP 反代: 座位/学生名单 JSON 改写
│   │     - 规则热更新 API (/__rules, 深合并)
│   ├── hijack_daemon.py    静默控制器 (start/stop/restart/status/reload)
│   │     - PID 文件管理, 双重进程验证, 端口竞态等待
│   ├── client_locator.py   客户端定位 (env > 注册表 > 常见路径 > lnk > 盘扫描)
│   ├── patch_asar.py       补丁引擎 (extract→patch→repack→copyback, 全程容错)
│   ├── netcheck.py         网络四步检查 (hosts/DNS污染/出站/代理, 每步有结论)
│   ├── hijack_on.bat       一键开: 定位→补丁→CA→hosts→代理 (全程日志)
│   ├── hijack_off.bat      一键关: 停代理→清hosts→删CA→还原asar (全程日志)
│   └── run_client.py 等
├── rules/hijack_rules.json 规则 (banner 改写/座位/计时器/封锁/passthrough)
├── docs/                   USAGE / PRINCIPLE / RULES_REFERENCE /
│                           TROUBLESHOOTING / CAMPUS_NETWORK
├── logs/                   on/off/patch/proxy 日志 + PID
└── backup/                 CA + 服务器证书
```

数据流:

```
教师手机 ──► 真实服务器 8.134.221.255
                 │ 下行帧 (横幅/座位/计时器/通知/热更包)
                 ▼
     代理 [TLS伪装 xlb.810086.com]
     ├─ broadcast.message → 横幅改写/丢帧/计时器强改
     ├─ class.data.* → 内嵌座位/名单改写
     ├─ renderer.pack.push → 丢弃 (禁热更新)
     └─ 其他帧透传
                 │ 改写后
                 ▼
            教室大屏客户端 (asar 已放宽证书, hosts 指向本机)
```

## 四、关键验证结果 (真实环境)

```
[hosts]     xlb.810086.com → 127.0.0.1 (确认)
[TLS]       补丁版 asar + 代理 CA → WS ESTABLISHED 持续在线
[上游]      经代理 → 官方 401 DEVICE_REGISTRATION_AUTH_MISSING (真实应答)
[横幅改写]  '你好这是一次测试' → '这不是测试' (大屏实测, TTS 同步)
[二维码]    POST /api/v2/auth/wechat/qrcode 经代理 → 363KB JSON 解析 OK
[头像]      GET /uploads/avatars/... 经代理 → 5117 字节 JPEG magic 正确
[恢复]      hijack_off 实测: 代理停/hosts清/CA删/asar还原 全部成功
```

## 五、遗留说明

- **头像显示**: 服务器 profile 里头像 URL 完好, 经代理拉取验证 OK。
  当前 teacher-avatars 缓存目录为空是历史失败遗留 (当时 asar 原版+证书失败时期),
  客户端重启后会自动重新下载 (rememberTeacherProfile → downloadAvatar)。
  若重启后仍无头像: 检查 `logs/hijack_proxy.log` 有无 `/uploads/` 请求,
  以及客户端日志 `avatar download failed`。
- **客户端自动更新**: 会覆盖 asar → 补丁失效 → "未连接"。重跑
  `bin\hijack_patch.bat` (自动定位+备份保护, 不会用补丁版覆盖原版备份)。
- **多域名容灾**: 若未来客户端内置备用域名/IP, hosts 需补充 (见 CAMPUS_NETWORK.md 原因6)。
