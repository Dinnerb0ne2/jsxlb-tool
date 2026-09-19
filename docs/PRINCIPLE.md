# 技术原理

## 一、总览: 数据流向

```
教师手机 ──► 真实服务器 (8.134.221.255, xlb.810086.com)
                  │
                  │ 下发 broadcast.message (横幅/指令/文件...)
                  ▼
        ┌─────────────────────┐
        │   劫持代理 (本机)     │
        │  hijack_proxy.py    │
        │                     │
        │  [规则引擎]          │
        │  - 横幅改写          │
        │  - 计时器强改        │
        │  - 座位/名单规则     │
        │  - 帧丢弃(禁止功能)  │
        └─────────────────────┘
                  │
                  │ 改写后的帧
                  ▼
            教室大屏客户端
```

客户端以为自己在和官方服务器对话; 官方服务器以为横幅已经送达。
代理夹在中间, 对下行 (服务器→客户端) 的每一帧做规则改写, 对上行 (客户端→服务器) 原样透传。

## 二、三个前提漏洞 (来自 SECURITY_REPORT.md, 经验证)

### 1. DNS 层: hosts 劫持

客户端所有请求都走 `getServerOrigin()`, 域名 `xlb.810086.com` 无证书钉扎。
往 hosts 写一行 `127.0.0.1 xlb.810086.com`, 客户端就把代理当官方服务器。

### 2. TLS 层: 证书校验可放宽

安全报告说"客户端全局关闭 TLS 校验"——**只对了一半**:

```js
// main.js:204/656
process.env.NODE_TLS_REJECT_UNAUTHORIZED = '0';   // 默认不校验
```

但关键连接 (WS 网关 main.js:6670、核心 API 多处) 显式覆盖:

```js
new WebSocket(rawGatewayUrl, { rejectUnauthorized: true });
new https.Agent({ rejectUnauthorized: true });
```

环境变量挡不住显式 `true`, 所以自签证书会被拒 → 客户端死循环重连 ("未连接服务器")。

**解决 (双轨)**:
1. 字节补丁 `app.asar`: 8 处 `rejectUnauthorized: true` → `rejectUnauthorized:false`
   (两者都是 24 字节, **等长替换** + 更新 header 里的 integrity, 不解包/不重打包, 毫秒级;
   原版备份为 app.asar.bak) — 覆盖 Node TLS 层 (axios/ws/https.request)
2. 自签 CA 装入 Windows 受信任根 — 覆盖 Chromium 网络栈 (渲染进程 img/fetch 等)

补丁与 CA 都由 start.bat 自动完成, end.bat 自动还原/删除。

### 3. 认证层: 客户端不校验服务器身份

客户端对服务器的 token 内容不做二次校验, 代理转发即可, 无需伪造身份。

## 三、代理实现要点

### 防自环 (最容易踩的坑)

hosts 把域名指到 127.0.0.1 后, **代理连上游时也被 hosts 骗回自己** → 死循环, 全部超时。
解法: 代理必须**绕过 hosts** 拿到真实 IP。采用多层解析链, 任何一层可用即可:

```
[1] 缓存         backup/upstream_ip.json  (上次成功 IP, 最快)
[2] 公共 UDP DNS  223.5.5.5 / 119.29 / 8.8.8.8  (校园网常封)
[3] 系统/校内 DNS  socket.getaddrinfo  (排除 hosts 返回的 127.0.0.1)
[4] DoH (443)     dns.alidns.com / doh.pub / cloudflare-dns.com
[5] 手动          --upstream-ip / XLB_UPSTREAM_IP / rules/upstream_ip.txt
```

每个候选 IP 都做 TCP 探活 443, 选可达的用; 成功后写缓存。
解析全失败时**不退出** (旧版会退出导致"未连接"), 而是打印手动指定指引并继续监听。

### WebSocket 双向泵

```
ws_pump(客户端→服务器, 不改)
ws_pump(服务器→客户端, 过规则引擎)
```

服务器→客户端的**每一条文本帧都过规则引擎** (`transform_downlink`): 类型封锁
(`block_ws_types`) 对任意 `payload.type` 生效, 横幅/命令/文件/计时器只命中对应类型,
`class.data.*` 额外改内嵌座位与名单; 其余帧原样回发。

### HTTP 反向代理

座位表 (`/api/v2/seats/display/<classId>`) 和学生名单 (`/api/v2/students?classId=`) 走 HTTP,
代理对 JSON 响应做同样的规则改写, 再回给客户端。

## 四、规则引擎作用点 (都有源码行号)

| 规则 | 客户端读取位置 | 改写效果 |
|---|---|---|
| 横幅 replace/remove/append | banner.html:587 `data.content` | 大屏文本 + TTS 语音同时改变 |
| force_sender | banner.html renderSender | 改发件人显示名 |
| force_tts | banner.html:590 ttsEnabled | 强制开/关语音 |
| timer.force_seconds | main.js:6127 getPopupDurationSeconds | 改横幅自动关闭倒计时 |
| seat.exclude/only | seat-display.html getSelectableSeats + main.js students 缓存 | 被移除的人不出现在座位表、不进随机点名袋 |
| seat.pairs | 座位 row/col 重排 | 结对学生强制相邻 |
| block_ws_types | main.js:6478 handleGatewayPayload | 帧被代理丢弃, 功能静默失效 |
| block_paths | HTTP 层 | 返回假成功 `{"success":true}`, 客户端无感知 |

## 五、为什么功能"静默失效"而不报错

封锁类规则 (禁横幅/禁热更新) 的实现是**代理丢帧**, 客户端根本收不到这条消息,
连重连/报错的路径都不会触发。这就是"禁止功能"的设计目标: 从网络层让功能不存在。

## 六、已验证的证据链

```
[补丁]   asar 8 处 rejectUnauthorized: true -> false (等长字节替换 + integrity 更新, 0.16s)
[TLS]    node rejectUnauthorized:true 客户端行为 + CA 信任 -> verify OK 200
[hosts]  socket.gethostbyname('xlb.810086.com') -> 127.0.0.1
[上游]   经代理 POST /api/v2/devices/register -> 官方 401 DEVICE_REGISTRATION_AUTH_MISSING
[WS]     [ws] client connected -> [ws] upstream: wss://xlb.810086.com/ws -> ESTABLISHED 持续在线
[改写]   BANNER rewrite: '你好这是一次测试' -> '这不是测试' (大屏实测)
```
