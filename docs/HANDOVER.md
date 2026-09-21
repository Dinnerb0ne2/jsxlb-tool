# 交接文档

## 一、这是什么

针对「教室小喇叭 AI 助手 v1.2.1」（Electron 教室大屏客户端）的**中间人劫持工具链**。

在客户端与官方服务器之间做透明代理，改写服务器下发的下行数据：
横幅文本、座位/点名、计时器、桌面控制命令、文件下发。

客户端与教师端无感知，上行流量（客户端→服务器）零改动。

漏洞依据见 `SECURITY_REPORT.md`；攻击链分析见 `docs/WRITEUP.md`。

## 二、交付清单

```
jsxlb/
├── start.bat / end.bat        一键开启 / 完全恢复 (双击, 自动提权)
├── requirements.txt           运行依赖清单 (aiohttp / cryptography)
├── src/                       核心库
│   ├── hijack_proxy.py        代理主程序 (TLS 终结 + 规则引擎 + 抗污染解析)
│   ├── patch_asar.py          客户端证书校验补丁 (等长字节替换, 毫秒级)
│   ├── client_locator.py      客户端安装目录定位 (5 级策略 + 特征校验)
│   └── ctl_common.py          一键流程共享操作 (hosts/CA/进程/asar)
├── scripts/                   CLI / 运维
│   ├── ctl_start.py           一键开启流程
│   ├── ctl_end.py             一键恢复流程
│   ├── hijack_daemon.py       代理进程控制 (start/stop/restart/status/reload)
│   ├── rules.py               规则 CLI (免手写 JSON / 免转义)
│   ├── inject.py              帧注入 CLI (banner/safety/teacher/command/raw/frames)
│   ├── watch.py               事件流监听 (tail -f, 只听关键事件)
│   ├── schedule.py            课表调度 CLI (import/status/check/force/on/off)
│   ├── autostart.py           开机自启 (install/remove/status/run; start/end 自动调)
│   ├── envcheck.py            环境预检 (解释器/依赖/端口; start 第 [0] 步调用)
│   ├── netcheck.py            网络诊断 (六步, 含 DNS 污染检测)
│   ├── run_client.py          静默启动客户端
│   ├── launch_log.py          带日志启动客户端 (排障)
│   └── install_info.py        打印客户端路径
├── bin/                       快捷 bat (提权壳, 逻辑都在 Python)
│   ├── hijack_on.bat / hijack_off.bat / hijack_patch.bat
│   ├── status.bat             状态一览 (含在线会话/捕获数)
│   ├── netcheck.bat / inject.bat   网络诊断 / 帧注入快捷入口
│   ├── autostart.bat          开机自启管理
│   └── start_all_silent.bat / stop_proxy_silent.bat
├── rules/hijack_rules.json    规则文件 (热更新)
├── rules/upstream_ip.txt      上游 IP 手动兜底 (校园网 DNS 全封时用)
├── docs/                      文档 (见第六节)
├── testsuite/local_sim.py     离线模拟器 (不连真实服务器)
├── testsuite/test_inject.py   集成测试 (假上游 + 真代理; 改写/注入/捕获断言)
├── poc/                       原始 PoC 资料 (rogue_server.py / installer.go)
├── app_extracted/             客户端解包源码 (分析参考, git 忽略)
├── backup/                    证书 + 原版 asar 存档 (git 忽略)
└── logs/                      运行日志 (git 忽略)
```

## 三、当前状态

### 已验证可用

| 组件 | 状态 |
|---|---|
| 代理启动 + 抗污染解析 | 通过（`upstream IP: 8.134.221.255 (来源: cached+verified)`） |
| asar 补丁 | 通过（8 处等长替换 + integrity 更新，0.16s） |
| 客户端定位 | 通过（`C:\Program Files\jsxlb`，其他 Electron 应用不会误匹配） |
| 横幅改写 | 曾实测通过（`BANNER rewrite: '你好这是一次测试' -> '这不是测试'`） |
| 帧注入 (不经教师端) | 通过（`testsuite/test_inject.py` 集成测试: 注入后客户端收到, `sent_to=1`） |
| 会话/帧捕获 API | 通过（同测试: `/__status.clients=1`, `/__frames` 含 rewrite / inject 记录） |
| SSL/TLS 通道 | 通过（`[ws] client connected` → `upstream: wss://...`） |
| 一键恢复 | 通过（停代理/清 hosts/删 CA/还原 asar 全部成功） |
| 规则 CLI | 通过（中文/布尔/热更新） |
| 自检 | `py -3 src/hijack_proxy.py --selftest` → ALL PASS |

### 未解决 / 待验证

**客户端登录后进不去主界面，弹出多个小窗口，停在登录页。**

> 本轮未改动此项：该问题在客户端侧（重装后 `auth.json` 丢失 → `Invalid class token`,
> main.js:5535），与工具链无关；仍需在真机上按下面第 1 步的二分法确认。

关键背景：客户端在 2025-09-14 20:25 被**重装**过，登录态（`auth.json`）丢失，需重新登录。

排查方向（按顺序，二分法最重要）：

1. **二分法定位责任方**（先做这个）
   ```
   双击 end.bat        (完全恢复: 停代理/清hosts/删CA/还原asar)
   启动客户端          (此时是直连官方服务器)
   登录测试
   ```
   - 直连也进不去主界面 → **与本工具无关**（客户端重装残留 / 服务器侧问题），
     检查 `%APPDATA%\jsxlb` 是否需清理，或找官方支持
   - 直连正常 → 是劫持链路引入的，继续第 2 步

2. **~~怀疑 `renderer.pack.push` 被拦~~ —— 已排除**
   旧版 ws_pump 只把 `type == "broadcast.message"` 的帧送进规则引擎，所以
   `block_ws_types` 对 `renderer.pack.push` 之类的帧**从未生效过**（该假设本身不成立，
   已于 2025-09-19 修好，见文末修复记录）。规则文件的 `block_ws_types` 现为 `[]`：
   ```bash
   py -3 scripts/rules.py show            # 确认 block_ws_types 是空的
   ```

3. **怀疑规则改坏了数据**
   ```bash
   py -3 scripts/rules.py flag passthrough on    # 直通: 代理在但不改任何东西
   ```
   - passthrough 下正常 → 逐条规则排查（重点看 `banner.types` 是否误包含业务帧类型）
   - passthrough 下仍不正常 → 与规则无关，回到第 1 步

4. **看现场日志**
   ```
   logs/hijack_proxy.log          有无 client connected / BLOCK / BANNER rewrite
   py -3 scripts/launch_log.py    带日志启动客户端 → logs/client_console.log
   ```
   注意：`hijack_daemon.py start` 会**追加**代理日志（重启前的记录保留）。

### 修复记录 2025-09-19（本轮代码修复，均已自检通过）

| # | 问题 | 修复 |
|---|---|---|
| 1 | `ws_pump` 只转 `broadcast.message` → `block_ws_types` 对其它类型永不命中、`class.data.*` 内嵌座位改写不可达 | 新增 `transform_downlink()`：**所有下行文本帧**都过规则引擎；`class.data.*` 改前缀匹配；`block_ws_types` 支持 `desktop.update.*` 前缀写法 |
| 2 | 默认值三处不一致（`rules.py` / `hijack_proxy.py` / 文档） | 统一为保守默认：`banner.types = [text,banner,notice]`、`block_ws_types = []`，同步改 README/USAGE/RULES_REFERENCE/CHEATSHEET |
| 3 | `ctl_start.py` 启动校验假阳性（`wait_port_free(443, 0)` 循环不执行 → 恒报 OK） | 新增 `ctl_common.wait_port_open()`（真实 443 探活，等 15s），失败即中止 |
| 4 | 细节：提示跑到 `bin/netcheck.py`（实际在 scripts/）、`netcheck.py` 死函数 `doh()`、`tmp/` 脚手架残留、文档里的“解包重打包”旧机制 | 已改正/删除 |

验证：`py -3 src/hijack_proxy.py --selftest` → ALL PASS（新增 3 组断言覆盖上述 #1）；
全量 `py_compile` 通过；代理非特权端口启动 + `GET /__rules` 烟雾测试通过。

### 修复记录 (本轮: 缺陷清理 + 注入/捕获功能)

**缺陷清理**（每条都有自检/集成测试兜底）：

| # | 问题 | 修复 |
|---|---|---|
| 1 | `resolve_upstream()` 末尾三元表达式两分支同值（死条件）；手动 IP 仍被污染过滤/证书验证否决，私网内合法上游会被丢弃 | 单一 return；手动指定**权威短路**（不再过滤/验证，操作者优先） |
| 2 | `debug.dump_dir` 单独开启无效（调用点被 `log_frames` 门禁卡死） | 两者任一开启即落盘 |
| 3 | `banner.types` 代码内兜底默认值仍含 `popup`（与统一默认不一致），且不支持通配 | 统一为 `text/banner/notice`；改用 `_type_match` → 支持 `notice*` 前缀 |
| 4 | `transform_http_json` 的 `/students?` 分支永不命中（`rel_url.path` 不含 query） | 按路径结尾判断；并加反向用例（`/students/export` 不改写） |
| 5 | `proxy_ws` 上游连接失败无处理（异常裸奔）；客户端注册在连接上游之后 | 异常捕获+日志+正常关闭；**先注册再连上游**（上游挂了注入仍可用） |
| 6 | `ws_pump` 异常静默、`close()` 无保护、上行帧不可观测 | try/except/finally 包裹；上行只记录不改（零篡改不变） |
| 7 | 规则/注入 API（明文端口）默认绑 `0.0.0.0`（同网段无鉴权可达） | 默认只绑 `127.0.0.1`，需放开用 `--admin-host 0.0.0.0` |
| 8 | `rules.py reload` 死行；`daemon stop` 注释与行为不符；status 事件过滤漏 INJECT | 已改正 |
| 9 | `ctl_common.set_readonly` 死变量；`kill_port_owners` 盲杀 443/8100 占用者 | 删死变量；先验命令行，非代理进程只警告不杀 |
| 10 | `doh_resolve` 死代码 | 删除 |
| 11 | `daemon stop` 杀不掉提权代理也报“已停止”, 还把 PID 文件删了 → 状态错乱、restart 起不来 (现场实测) | stop 改为**核实结果** (PID 失效时从 443/8100 反查孤儿); 失败如实报错并提示提权; restart 失败即中止; start 增加孤儿占用检测 |
| 12 | 校园现场: `[6] proxy NOT listening on 443` 只报现象不报原因 —— 真相是 `F:\jsxlb` 的 Python 缺 aiohttp, 代理启动瞬间死 | start 新增 `[0]` 环境预检 (`envcheck.py --ensure`, 缺依赖自动 pip 装); `[6]` 失败时**贴代理日志尾部**并给两条常见原因; 新增 requirements.txt |

**新增能力**：

| 能力 | 入口 | 说明 |
|---|---|---|
| 帧注入（不经教师端） | `POST /__inject` + `scripts/inject.py` | banner/safety/teacher/command/raw 五种写法；`--apply-rules` 可选过规则引擎 |
| 会话与帧捕获 | `GET /__status` / `GET /__clients` / `GET /__frames` | 内存环 `debug.capture_max`（默认 200）；动作标记 pass/rewrite/drop/up/inject |
| 事件流监听 | `scripts/watch.py` | tail -f，只看关键事件（改写/封锁/注入/连接） |
| 集成测试 | `testsuite/test_inject.py` | 假上游 + 真代理 + WS 客户端：改写/注入/捕获/CLI 四段断言 |
| 课表调度 (三个弹窗) | `schedule` 规则段 + `scripts/schedule.py` | 按导入课表: 课间显示、课上收起(课间自动补发)或丢弃; `/__status` 暴露状态与折叠队列 |
| 阻止客户端自动更新 (默认开启) | `update` 规则段 | 三层防线: 服务器判定接口假应答 + `/desktop-updates/*` 404 + `desktop.update.*` 丢帧; 防 asar 补丁被更新覆盖 |
| 开机自启 (随客户端一起起) | `scripts/autostart.py` (start.bat 注册 / end.bat 注销) | 计划任务 SYSTEM+HIGHEST+ONSTART; 开机自愈 hosts / CA / asar / 代理; 代理支持延迟解析上游 (网络比代理晚就绪也能自恢复) |
| 环境预检 + 依赖自补 | `scripts/envcheck.py` + `requirements.txt` | start 第 [0] 步自动 `--ensure`; 失败时把真原因 (代理日志尾部) 顶到 start_end.log; 换机器/换 Python 不再瞎猜 |

验证：`--selftest` ALL PASS（扩到 20 组断言）；`testsuite/test_inject.py` PASS；
全量 `py_compile` 通过。

## 四、使用方法

```
一键开启:  双击 start.bat      (自动提权: 定位→补丁→CA→hosts→代理→客户端)
一键恢复:  双击 end.bat        (自动提权: 停代理→关客户端→还原asar→删CA→清hosts)

看状态:    py -3 scripts/hijack_daemon.py status
改规则:    py -3 scripts/rules.py add banner.replace "原文" "改文"
           py -3 scripts/rules.py reload
网络诊断:  py -3 scripts/netcheck.py
帧注入:    py -3 scripts/inject.py banner "文本" --sender 王老师
抓帧:      py -3 scripts/inject.py frames 50     /    py -3 scripts/watch.py
```

规则速查（完整字段见 `docs/RULES_REFERENCE.md`）：

| 需求 | 规则 |
|---|---|
| 横幅替换/删除/追加 | `banner.replace / remove / append` |
| 禁止横幅 | `banner.block_banner: true` |
| xx 与 xx 永远同桌 | `seat.pairs: [["小明","小红"]]` |
| 某人永不被点名 | `seat.exclude: ["张三"]` |
| 改横幅时长 | `timer.force_seconds: 9999` |
| 拦锁屏/关机/抓拍 | `commands.block` / `commands.block_snapshot` |
| 预演（只记录不改） | `debug.dry_run: true` |
| 直通（代理在但不干预） | `passthrough: true` |

## 五、架构要点

```
教师端 ──► 官方服务器 (8.134.221.255 / xlb.810086.com)
                │ 下行帧
                ▼
        代理 (伪装 xlb.810086.com, TLS 自签 CA)
         ├─ broadcast.message → 横幅改写 / 计时器 / 命令拦截 / 文件拦截
         ├─ class.data.*     → 内嵌座位与名单改写
         ├─ block_ws_types  → 命中即丢帧 (任意 payload.type)
         ├─ /__inject        → 帧直推客户端 (不经服务器, 无需教师端)
         ├─ /__frames        → 捕获环 (down/up/inject + pass/rewrite/drop)
         └─ 其余帧原样转发
                │
                ▼
         教室客户端 (asar 已放宽证书校验, hosts 指向本机)
```

三个前提（详见 `SECURITY_REPORT.md`）：

1. **DNS**: 客户端无域名钉扎 → hosts 可重定向
2. **TLS**: 客户端 `NODE_TLS_REJECT_UNAUTHORIZED='0'`，但关键连接显式 `rejectUnauthorized: true`
   → 必须打 asar 补丁（8 处改为 false）才能过证书校验
3. **认证**: 代理透传 token，无需伪造身份

关键实现决策：

- **防自环**: hosts 劫持后代理连上游会被自己的 hosts 骗回自身
  → 启动时用 UDP 原始 DNS + 系统 DNS + DoH 多路解析，并用 **TLS 证书验证**筛掉 DNS 污染假 IP
- **asar 补丁走等长替换**: `rejectUnauthorized: true` 与 `:false` 均为 24 字节
  → 直接字节替换 + 更新 integrity，无需解包/重打包/Node.js（0.16s）
- **补丁状态判断必须用内容**（main.js 区间有无 needle），不能用文件大小（等长替换前后一致）

## 六、文档索引

| 文档 | 内容 |
|---|---|
| `docs/WRITEUP.md` | 攻击分析报告（漏洞链 / 验证证据 / 缓解建议） |
| `docs/PRINCIPLE.md` | 技术原理 |
| `docs/CONFIG_GUIDE.md` | 配置文件填写详解 (字段逐条 / 课表调度 / 配方 / 常见错填) |
| `docs/USAGE.md` | 使用手册 |
| `docs/RULES_REFERENCE.md` | 规则字段完整参考 + 规则 CLI |
| `docs/TROUBLESHOOTING.md` | 故障排查 |
| `docs/CAMPUS_NETWORK.md` | 校园网专项（DNS 封锁/污染） |
| `docs/CHEATSHEET.md` | 命令速查 |
| `docs/MISSION_REVIEW.md` | 方法论与踩坑复盘 |
| `SECURITY_REPORT.md` | 原始安全分析报告（行号级证据） |

## 七、注意事项

- **git**: 仓库在 `D:/MTY/Code/hack/jsxlb`。`.gitignore` 已排除
  `logs/ backup/ app_extracted/ poc/_legacy/ tmp/ *.bak`。
  `backup/` 含自签 CA **私钥**，绝不能入库。
- **个人隐私**: 代码与文档中的本机路径、班级号、学校信息已清理为中性值。
  新增内容时注意不要把真实环境信息写回代码/文档。
- **代码风格**: 不使用 `====` 长分隔线等装饰性输出；保持注释精简。
- **未提交**: 已有 1 个提交 `8bf4a8b`（工具链首次入库）；本轮又改了
  `src/hijack_proxy.py` / `src/ctl_common.py` / `scripts/*` / `docs/*` / `rules/hijack_rules.json`，
  新增 `scripts/inject.py`、`scripts/watch.py`、`testsuite/test_inject.py`、
  `bin/netcheck.bat`、`bin/inject.bat`。全部仍未提交（以 `git status` 为准）。

## 八、快速环境重建（换机器时）

```
1. 安装 Python 3.10+ 与 Node.js (仅 asar 回退路径需要)
2. 拷贝整个 jsxlb/ 目录
3. 装客户端 (任意位置, 工具会自动定位)
4. 双击 start.bat     (会重新生成 CA 证书并完成全部部署)
5. 若校园网 DNS 全封: 在 rules/upstream_ip.txt 写一行真实 IP 再 start.bat
```
