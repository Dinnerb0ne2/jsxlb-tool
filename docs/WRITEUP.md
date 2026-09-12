# 教室小喇叭 AI 助手 v1.2.1 — 安全分析 Write-Up

> 目标: 教室小喇叭 AI 助手 (Electron 教室大屏客户端) v1.2.1
> 授权范围: 授权安全交战 (本工作区内) — 教室大屏客户端 + 官方网关 xlb.810086.com
> 攻击者模型: 已获教室客户端本机执行权限 / 同网段可劫持 DNS 的攻击者
> 最终影响: 对"服务器 → 教室大屏"下行链路的**透明内容劫持** (横幅改写/座位操纵/计时器篡改/功能禁止)

---

## 1. 资产与攻击面

### 1.1 系统组成

```
教师手机/教师端 App ──► 官方网关 (xlb.810086.com / 8.134.221.255)
                              │ wss://xlb.810086.com/ws (WebSocket 实时通道)
                              ▼
                   教室大屏客户端 (Electron)
                   ├─ main.js        (15512 行, 主进程)
                   ├─ renderer/*.html (banner/seat-display/dashboard 等)
                   ├─ utils/edge-tts.js 等
                   └─ node_modules   (ws / axios / electron-updater)
```

### 1.2 攻击面地图 (探测自 app.asar 解包源码)

| # | 入口 | 协议 | 认证 | 敏感汇聚点 |
|---|---|---|---|---|
| A | `/ws` 网关 | wss | Bearer token (零验证信任) | 广播帧 → 桌面控制/横幅/文件 |
| B | `/api/v2/devices/*` | https | classToken | 设备注册/心跳/退出审批 |
| C | `/api/v2/seats/*` | https | sessionToken | 座位表数据 |
| D | `/api/v2/students` | https | sessionToken | 随机点名学生名单 |
| E | `/api/v2/auth/wechat/qrcode` | https | 无 | 微信扫码登录 |
| F | `/desktop-updates/` | https | 无 (无签名验证) | 更新包下发 |

### 1.3 信任决策模型

关键发现: **信任决策几乎全部下沉在传输层** — 客户端不校验:
- 网关指令的来源 (任何能进通道的帧都执行)
- 服务器身份 (TLS 校验可被放宽)
- 广播内容的完整性 (内容原样渲染/执行)

---

## 2. 漏洞链 (分析→验证)

### 2.1 TLS-1: 全局 TLS 校验关闭 [部分成立, 经验证修正]

```js
// main.js:204, 656
process.env.NODE_TLS_REJECT_UNAUTHORIZED = '0';
```

**原始报告结论**: 客户端所有 TLS 连接不校验证书。

**实际验证结论 (修正)**: 该环境变量只影响**默认** TLS 配置。关键路径显式覆盖:

```js
// main.js:6670  WS 网关 (ws 库)
new WebSocket(rawGatewayUrl, { rejectUnauthorized: true });
// main.js:2286/5496/5555/7406/7446  axios https.Agent
new https.Agent({ rejectUnauthorized: true });
// main.js:5619/11065  https.request
rejectUnauthorized: true
```

→ 全局变量在这些路径上**无效**。自签证书被拒 → 客户端 WS 反复重连 (日志 186 次失败)。

**利用方式**: 解包 app.asar → 将全部 8 处 `rejectUnauthorized: true` 改 `false` → 重打包 (备份原文件)。

### 2.2 DNS-1: 服务器地址无域名钉扎

```js
// main.js:1669
const rawServerUrl = process.env.DESKTOP_BROADCAST_SERVER_URL
  || process.env.SERVER_URL
  || 'https://xlb.810086.com';
// main.js:2323 getServerOrigin() 全部 API/WS 基于此
```

→ 客户端解析 `xlb.810086.com` 的**结果**决定它连谁。改 hosts 即重定向整个通信面。

### 2.3 AUTH-1: 设备身份零来源校验

网关 `identity` 帧 (main.js:6722-6733) 只带 `classId + deviceUid + token`,
token 正确性由服务端判断, 客户端不验证对端。代理**透传**身份帧即可冒充完整会话。

### 2.4 GATE-1: 广播通道 = 远程控制通道 [验证]

main.js:6568 `broadcast.message` → `executeRemoteSystemControlAction()`:
`lock_system` (锁屏), `shutdown_system` (关机), `lock_app/unlock_app`, `smart-attendance:snapshot` (摄像头)。

**前提是进入通道**, 中间人天然持有该前提。

### 2.5 FILE-1: 广播文件路径穿越 [验证于本地模拟]

```js
// main.js:12530
const filePath = path.join(downloadPath, messageData.fileName);  // 无 basename 清洗
```
`fileName` 含 `..` 即任意路径写。

### 2.6 RCE-2: edge-tts 命令注入 [本地模拟验证]

```js
// utils/edge-tts.js:141
exec(`npx edge-tts --voice "${voiceName}" --rate="${rateStr}" --volume="${volumeStr}" --text "${escapedText}" ...`)
```
Windows cmd 引号规则下 `\"` 不闭合, `&` 可 break out。

---

## 3. 实际采用的攻击链: 透明下行劫持 (本文档 PoC 工具链)

攻击面组合选择: **TLS-1 + DNS-1 + AUTH-1** — 不需要 RCE, 实现持久可逆的
内容层中间人。选择理由:
1. 风险可控 (不改二进制逻辑, 只放宽校验)
2. 可逆 (备份 + 一键恢复)
3. 效果覆盖所有"服务器下发"内容, 一条链路多种玩法

### 3.1 架构

```
攻击机 (与教室大屏同网段/同机)
┌────────────────────────────────────────────┐
│  hijack_proxy.py  (MITM 代理)               │
│   ├─ 监听 443 (TLS, 自签 CA 签发证书)        │
│   ├─ 伪装 CN=xlb.810086.com                 │
│   ├─ 上行 (客户端→真实服务器): 原样透传       │
│   ├─ 下行 (真实服务器→客户端): 规则引擎改写    │
│   └─ 规则热更新 API (端口 8100)              │
└────────────────────────────────────────────┘
        │                          ▲
 hosts 劫持: 127.0.0.1 xlb.810086.com
        ▼                          │ 直连真实 IP (防自环)
教室大屏客户端               官方网关 8.134.221.255
```

### 3.2 利用步骤 (一键脚本化)

| 步骤 | 动作 | 脚本 |
|---|---|---|
| 1 | 定位客户端安装目录 (注册表/路径/快捷方式/扫描) | `client_locator.py` |
| 2 | 解包 asar → 8 处 `rejectUnauthorized:false` → 重打包 | `patch_asar.py` |
| 3 | 生成 CA + 签发 `xlb.810086.com` 服务器证书 | `hijack_proxy.py` 首次运行 |
| 4 | CA 装入 Windows 受信任根存储 | `ctl_start.py` |
| 5 | hosts 加 `127.0.0.1 xlb.810086.com` | `ctl_start.py` |
| 6 | 静默启动代理 (daemon 管理) | `hijack_daemon.py` |
| 7 | 启动客户端 (detached) | `run_client.py` |
| — | **一键**: 双击 `start.bat` (自动提权, 1-7 全自动) | |
| — | **一键恢复**: 双击 `end.bat` (停代理/杀客户端/还原 asar/删 CA/清 hosts) | |

### 3.3 关键工程点 (踩坑与解决)

| 工程问题 | 现象 | 解决 |
|---|---|---|
| MITM 自环 | hosts 劫持后代理连上游也被骗回自身 → 全部超时 | 启动时 UDP 原始 DNS (223.5.5.5) 绕过 hosts 取真实 IP, 固定直连 |
| 显式 rejectUnauthorized | WS 网关证书拒绝 → 客户端"未连接" | asar 补丁 8 处 → false (不是只靠 NODE_TLS_REJECT_UNAUTHORIZED) |
| asar 路径含空格 | `C:\Program Files\jsxlb` 使 npx-asar 崩 | 复制到项目内无空格 tmp/ 目录操作, 再回拷 |
| Program Files 只读 | shutil 写 asar 被拒 | SetFileAttributesW 去只读 → 写 → 恢复只读 |
| 代理孤儿进程 | daemon 重启端口占用 10048 | ctl 脚本 kill_port_owners 按 netstat PID 清理 |
| 端口竞态 | restart 后立刻 bind 失败 | wait_port_free 轮询端口释放 |
| bat 中文编码 | GBK 控制台乱码/taskkill 中文 exe 名失效 | 全部逻辑入 .py, bat 仅做提权壳 |

---

## 4. 验证证据 (全部实测)

| 验证项 | 方法 | 结果 |
|---|---|---|
| hosts 劫持生效 | `socket.gethostbyname('xlb.810086.com')` | `127.0.0.1` |
| 真实 IP 解析 | UDP DNS 3 服务器交叉 (223.5.5.5/119.29.29.29/8.8.8.8) | `8.134.221.255` 一致 |
| 代理穿透上游 | 经代理 POST /register | 官方 `401 DEVICE_REGISTRATION_AUTH_MISSING` (真实应答) |
| WS 通道 | 代理日志 | `client connected` → `upstream: wss://.../ws` → ESTABLISHED |
| 证书链验证 | node rejectUnauthorized:true + CA 信任 | `verify OK, status 200` |
| **横幅改写** | 教师端发「你好这是一次测试」 | 代理日志 `BANNER rewrite` → 大屏显示「这不是测试」+ 语音同步 |
| 尾部追加 | append 规则 "喵~" | 大屏显示 "...喵~" |
| 二维码 API | 经代理 POST /auth/wechat/qrcode | 200 + 363KB JSON 解析成功 |
| 头像下载 | 经代理 GET /uploads/avatars/... | 5117 字节 JPEG (magic 0xFFD8FF) |
| 一键 END 恢复 | end.bat 全流程 | asar 还原 + CA 删除 + hosts 清理 + 代理停止 (逐项日志) |
| 一键 START | start.bat 全流程 | 定位→补丁→CA→hosts→代理→客户端 → `[ws] client connected` |

---

## 5. 影响与缓解

### 5.1 影响总结

攻击者获得对教室大屏"看到什么、听到什么、被抽到谁"的完全控制,
且**服务器与客户端都无法察觉** (上行零篡改, 服务器看到的是正常客户端行为)。

### 5.2 缓解建议 (按优先级)

1. **[P0] 移除全局 NODE_TLS_REJECT_UNAUTHORIZED 依赖 + 客户端内置证书钉扎**
   (公钥固定, 而非仅 rejectUnauthorized 开关)
2. **[P0] 服务器下发帧增加来源认证** (HMAC + 时间戳, 防重放), 客户端侧校验
3. **[P1] 域名证书校验恢复默认**, 移除所有 `rejectUnauthorized: false` 后门
4. **[P1] 主进程对广播内容的 schema 校验** (消息类型白名单下沉到客户端)
5. **[P2] 客户端文件完整性自检** (asar 签名/哈希, 检测补丁)
6. **[P2] 教室端网络接入要求** (禁用学生可写的 hosts/DNS 配置, 802.1X)

---

## 6. 目录结构 (完整工具链, 可迁移)

```
jsxlb/                          ← 本工具链根目录 (整体拷贝即可迁移)
├── README.md                   总览
├── start.bat                   一键开启 (自动提权)
├── end.bat                     一键关闭/恢复 (自动提权)
├── bin/
│   ├── hijack_proxy.py         MITM 代理 + 规则引擎
│   ├── hijack_daemon.py        静默进程控制器
│   ├── ctl_start.py / ctl_end.py / ctl_common.py  一键流程(提权执行)
│   ├── client_locator.py       客户端自动定位
│   ├── patch_asar.py           asar 证书校验补丁
│   ├── netcheck.py             网络四步检查
│   ├── run_client.py / launch_log.py
├── rules/hijack_rules.json     劫持规则 (热更新)
├── docs/                       本目录全部文档 + WP
├── backup/                     证书 + 原版 asar (灾难恢复)
└── logs/                       全程操作日志
```

## 7. 附: 验证用最小回放 (复现横幅改写)

```bash
# 1. 起代理 (管理员)
start.bat

# 2. 确认客户端已连 (代理日志出现 [ws] client connected)

# 3. 教师端发横幅 → 内容含 "你好这是一次测试"

# 4. 验证改写 (代理日志)
grep "BANNER rewrite" logs/hijack_proxy.log
#    BANNER rewrite: '你好这是一次测试' -> '这不是测试喵~'

# 5. 恢复
end.bat
```
