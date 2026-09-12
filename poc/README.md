# 教室小喇叭AI助手 v1.2.1 — PoC 使用说明

## 文件
- `rogue_server.py`  恶意后端 (aiohttp)。模拟 API + WebSocket 网关,接收客户端连接后下发攻击指令。
- `installer.go`     伪造更新安装包(弹 MessageBox 证明 RCE)。`go build -ldflags "-H=windowsgui" -o installer.exe installer.go`
- `start_rogue_lab.bat` 一键复现环境。

## 快速开始
```
cd poc
pip install aiohttp
go build -ldflags "-H=windowsgui" -o installer.exe installer.go   # 仅 --update-rce 需要
start_rogue_lab.bat
```
客户端连入后,在 rogue server 控制台输入:
| 编号 | 攻击 | 复现的漏洞 |
|---|---|---|
| 1 | lock_system | 远程锁屏 (rundll32 LockWorkStation) |
| 2 | shutdown_system | 远程关机倒计时 (shutdown /s /t 0) |
| 3 | lock_app | 强制 kiosk 应用锁 (客户端万能码硬编码 810086) |
| 4 | traversal write | 文件下载路径穿越 → 任意路径写文件 |
| 5 | TTS injection | edge-tts 命令注入 → 弹计算器 (RCE) |
| 6 | silent snapshot | 隐藏窗口静默抓拍教室摄像头 |
| 7 | silent drop | 静默下载文件到桌面 |
| 8 | fake update | 伪造更新链 → 静默执行任意 exe (RCE) |

## MITM 变体(不使用环境变量)
同网段 ARP 欺骗 + 自签证书即可,因为客户端:
- `process.env.NODE_TLS_REJECT_UNAUTHORIZED = '0'` (main.js:204,656)
- 所有 axios 请求显式走 getServerOrigin(),无证书钉扎
