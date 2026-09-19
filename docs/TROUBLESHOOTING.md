# 故障排查手册

排查前先跑 `py -3 scripts\netcheck.py` (网络四步) + 看 `logs/start_end.log` / `logs/hijack_proxy.log`。
**凡一键脚本类问题, 先把 `logs/start_end.log` 内容发出来** —— 每步都有日志, 一眼定位失败点。

## 症状速查

### 客户端显示"未连接服务器"

| # | 原因 | 判定方法 | 处理 |
|---|---|---|---|
| 1 | 代理没跑 | `netstat -ano \| findstr :443 \| findstr LISTEN` 空 | 双击 `start.bat` |
| 2 | hosts 没生效 | `Resolve-DnsName xlb.810086.com` ≠ 127.0.0.1 | `start.bat` (会加 hosts + flushdns) |
| 3 | asar 补丁失效 (原版/更新覆盖) | `logs/hijack_proxy.log` 无 `client connected`, 客户端日志有 `handshake failed` | 双击 `start.bat` (自动重打补丁) |
| 4 | 代理-真实服务器断网 | `logs/hijack_proxy.log` 大量 `upstream error`; `netcheck.py` 第 3 步 BLOCKED | 检查本机外网; 校园网见 CAMPUS_NETWORK.md |
| 5 | 客户端是旧实例/多实例 | 多个 jsxlb 进程或启动后不进代理 | `end.bat` 杀干净 → `start.bat` 重来 |
| 6 | **上游 IP 解析失败** | 代理日志 `上游 IP 全部解析失败`; `netcheck.py` | 见下方"DNS 全失败"一节 |

### DNS 全失败 (校园网常见)

现象: `netcheck.py` 第 [2] 步三个公共 DNS 全 FAIL。

**先看第 [3] 步 (系统/校内 DNS)** —— 只要它有 IP 且 [5] 步 REACHABLE, 代理能自动使用,
不需要你做任何事 (点开始 `start.bat` 就行)。

只有 [2][3][4] 全失败时才需要手动指定:

```
# 在 rules/upstream_ip.txt 写一行真实 IP (去掉行首 #), 例:
8.134.221.255
# 或启动参数: py -3 src\hijack_proxy.py --upstream-ip 8.134.221.255
```

代理每次成功解析会缓存到 `backup/upstream_ip.json`, 下次优先用缓存 —— 即使 DNS 被封也能直接跑。

完整分析 → [CAMPUS_NETWORK.md](CAMPUS_NETWORK.md)

PowerShell 快速验证:

```powershell
Test-NetConnection 127.0.0.1 -Port 443     # True = 代理活着
Resolve-DnsName xlb.810086.com             # 应为 127.0.0.1
```

### 教师端显示教室离线 (教室端界面正常)

代理→真实服务器连接断了:

```powershell
Get-Content <本项目目录>\logs\hijack_proxy.log -Tail 20
```

全是 `upstream error` → 外网问题; 日志安静但会话结束 → 重启客户端 (WS 断线重连由客户端自动, 也可 `end.bat`+`start.bat`)。

### 横幅没被改写

1. 看 `logs/hijack_proxy.log` 有无 `BANNER rewrite` 行
2. 有 → 规则生效了, 检查规则是否满足你的预期 (比如 append 被 replace 消费)
3. 没有 → 依次查:
   - 是否 `passthrough: true`
   - 规则是否已热更 (POST /__rules 或 `py -3 scripts\hijack_daemon.py reload`)
   - 是否被课表调度压下了 (`schedule.targets` 的 `break_only` + 上课中) —— 看日志 `SCHED fold` / `SCHED drop`
   - 横幅 messageType 是否在改写列表 (text/banner/notice), 不在则加进去或写 `notice*` 这种前缀
   - 客户端是否真的走了代理 (见上表 #5)

### 座位/点名规则没生效

- 名字必须与服务器数据**完全一致** (含全角/空格), 用日志 `SEAT pair/exclude` 验证是否执行
- 客户端有 180 天本地缓存 (`CLASS_DATA_CACHE_TTL_MS`): 改规则后需教师端改动一次
  座位/学生触发 `class.data.updated` 清缓存, 或重启客户端
- 若走的是托盘随机点名 (students API), 用 `STUDENTS rewritten` 日志确认名单被改

### 规则文件改了不生效

规则是**内存态**, 文件只在启动时加载。必须热更新:

```bash
curl -X POST http://127.0.0.1:8100/__rules -H "Content-Type: application/json" -d @rules/hijack_rules.json
# 或
py -3 scripts\hijack_daemon.py reload
```

**新字段没反应?** 代理是不是升级代码之前启动的 —— 旧进程只认它启动时那一版代码
(`schedule`、`/__inject`、`/__frames` 在旧进程里根本不存在)。重启即可:

```powershell
py -3 scripts\hijack_daemon.py restart
```

### 代理是提权启动的, 普通权限杀不掉

现象: `hijack_daemon.py stop` / 手动 `taskkill /F /PID <pid>` 报 `拒绝访问` (Access denied),
但旧版 daemon 仍会打“已停止”并删掉 PID 文件 —— 看起来停了, 其实端口还被占, `restart` 也起不来。

处理: 以管理员运行 `bin\hijack_on.bat` (先杀后拉一键完成) 或提权跑 `hijack_daemon.py restart`。
新版 daemon 会**核实杀没杀掉**, 失败时如实报错并给出提示, 不再报假成功。

### start.bat / end.bat 点了没反应或失败

1. **UAC 弹窗**: 双击后应弹提权确认, 点"是"。没弹 → 该 bat 在受限环境, 右键"以管理员身份运行"
2. **看日志**: `logs/start_end.log` 每步打点, `[!]` 行即失败点
3. **常见失败**:
   - `[3] patch FAILED` → 看 `logs/hijack_patch.log` (多为权限/残留/网络 npx 下载问题)
   - `[6] proxy NOT listening` → 端口被占, 先 `end.bat` 清干净再 `start.bat`
   - 手动杀代理报 `拒绝访问` / `Access denied` → 代理是**提权启动**的, 普通权限 `taskkill` 杀不掉:
     以管理员运行 `bin\hijack_on.bat` (或 `end.bat`), 或提权跑 `hijack_daemon.py restart`
   - Python 找不到 → `py -3` 不可用, 换 `python` 或装 Python 并勾选 PATH

### 客户端启动崩溃 / 白屏

多半是 asar 损坏 (更新与补丁互踩):

```powershell
# 客户端目录自动定位
py -3 scripts\install_info.py
# 手动还原 (路径替换为上一条输出)
copy "<目录>\resources\app.asar.bak" "<目录>\resources\app.asar"
# 重新补丁
py -3 src\patch_asar.py     (需管理员)
```

## 完全恢复 (三步)

双击 `end.bat` 一步到位:

```
[1] 停代理 (daemon + 孤儿清理)
[2] 关客户端 (全部 jsxlb 进程)
[3] 还原 asar (自动处理只读属性)
[4] 删除 CA
[5] 清理 hosts + flushdns
```

或手动: `bin\hijack_off.bat` (旧式等价入口)。

执行后系统回到劫持前, 客户端直连真实服务器。

## 日志文件说明

| 文件 | 内容 |
|---|---|
| `logs/start_end.log` | ★ start/end 一键流程逐步日志 (排障首选) |
| `logs/hijack_on.log` / `hijack_off.log` | 旧式 on/off 脚本日志 |
| `logs/hijack_patch.log` | asar 补丁详细输出 (extract/patch/pack/copyback) |
| `logs/hijack_proxy.log` | 代理运行: 连接/改写/丢帧/上游错误 |
| `logs/hijack_proxy.pid` | 代理进程 PID |
| `logs/client_console.log` | 客户端主进程日志 (launch_log.py 启动才有) |

日志关键行:

```
[ws] client connected        客户端连入代理
[ws] upstream: wss://...     代理连上真实服务器
BANNER rewrite: 'a' -> 'b'   横幅被改写 (完整内容, 无截断)
frame DROPPED (id=...)       帧被丢弃 (禁止功能)
TIMER forced: Ns             计时器被强改
SEAT pair/exclude            座位规则执行
STUDENTS rewritten           点名名单改写
upstream error               代理到真实服务器断连
INJECT -> N client(s)        帧注入下发 (不经教师端)
[ws] upstream connect failed  客户端连入但代理连不上上游 (注入仍可用)
```

## 代理自身日志等级

`hijack_proxy.py` 每次改写/丢帧都打印。若需更细 (看透传帧内容),
可在 `transform_broadcast_frame` 前加临时 debug 打印, 或在 `ws_pump` 里对
非 broadcast 帧也打 `[tx] <type>`。排查完删除, 避免日志刷屏。

## 注入没反应 / 捕获为空

| 症状 | 原因 | 处理 |
|---|---|---|
| `inject.py` 报"代理不可达" | 代理没跑, 或端口不是 8100 | `py -3 scripts\hijack_daemon.py status`; 自定义端口加 `--port` |
| `sent_to=0` | 没有客户端连在代理上 | 看 `__status.clients`; 双击 `start.bat` 并确认客户端已连上代理 |
| 注入被丢弃 | 加了 `--apply-rules` 且命中 block 规则 | 看 `INJECT` / `frame DROPPED` 日志; 去掉 `--apply-rules` 或改规则 |
| `frames` 为空 | `debug.capture_max` = 0, 或热更后没新流量 | 设回 `200` + `reload`, 然后触发一条横幅/注入 |
| 注入 API 外部访问不到 | 明文端口默认只绑 127.0.0.1 (安全默认) | 需给同网段用时: 代理加 `--admin-host 0.0.0.0` |
| 捕获里看不到上行帧 | 上行记录也受 `capture_max` 限制 | 同上 |
