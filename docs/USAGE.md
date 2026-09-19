# 使用手册 (How-To)

> 从部署到日常到恢复, 照着做即可。工具链整体位于本目录 (jsxlb/),
> 所有脚本/日志/规则/备份都在目录内, 整目录拷贝即可迁移。

## 前置条件

- Python 3.10+ (`py -3` 可用)
- Node.js (patch_asar.py 用 npx @electron/asar 解包/打包)
- 目标: 教室小喇叭客户端已安装 (位置自动定位, 也可 `XLB_CLIENT_DIR` 指定)

```bash
pip install aiohttp cryptography
```

## 一、一键开启 (推荐)

```
双击 start.bat
```

自动完成 (全程写 `logs/start_end.log`, 每步可见):

```
[1] 定位客户端安装目录 (注册表/常见路径/快捷方式/盘符扫描)
[2] 清理残留客户端进程 (防止旧会话/幽灵窗口)
[3] asar 补丁: 8 处 rejectUnauthorized: true -> false (等长字节替换 + integrity 更新, 原版备份 app.asar.bak)
    - 已补丁则跳过 (大小对比判断, end 还原后会自动重打)
[4] CA 信任: 自签 CA 装入 Windows 受信任根 (已信任则跳过)
[5] hosts 劫持: 127.0.0.1 xlb.810086.com (已存在则跳过) + flushdns
[6] 静默启动代理 (daemon, PID 文件管理; 先清理孤儿代理进程防端口占用)
[7] 静默启动客户端 (detached 无窗口)
```

任一步失败 → 立即中止并打印原因, 不会带病继续。

客户端显示"已连接服务器"即成功 —— 它连的是本地代理, 代理转发真实服务器。

## 二、日常使用

| 操作 | 做法 |
|---|---|
| 看状态 | `py -3 scripts\hijack_daemon.py status` (代理 PID/规则/最近改写事件) |
| 一键健康检查 | 双击 `bin\status.bat` |
| 改规则 | 编辑 `rules\hijack_rules.json` → `py -3 scripts\hijack_daemon.py reload` |
| 热更规则 API | `curl -X POST http://127.0.0.1:8100/__rules -H "Content-Type: application/json" -d @rules\hijack_rules.json` |
| 查看当前规则 | 浏览器开 `http://127.0.0.1:8100/__rules` |
| 只停代理 (保留客户端) | `py -3 scripts\hijack_daemon.py stop` |
| 重启代理 | `py -3 scripts\hijack_daemon.py restart` |
| 带日志启动客户端 (排障) | `py -3 scripts\launch_log.py` → 日志 `logs\client_console.log` |
| 网络四步诊断 | `py -3 scripts\netcheck.py` |
| 帧注入 (不经教师端) | `py -3 scripts\inject.py banner "文本" --sender 王老师` |
| 抓帧 / 看捕获 | `py -3 scripts\inject.py frames 50` |
| 事件流监听 | `py -3 scripts\watch.py` |

## 三、一键恢复 (end)

```
双击 end.bat
```

自动完成 (日志 `logs/start_end.log`):

```
[1] 停代理 (daemon + 清理孤儿监听进程)
[2] 关闭客户端 (全部 jsxlb 进程)
[3] 还原 asar (app.asar.bak → app.asar, 处理只读属性)
[4] 删除 CA (Windows 根存储)
[5] 清理 hosts + flushdns
```

执行后系统完全回到劫持前: 客户端直连真实服务器, 无任何残留。

## 四、手动等价入口 (脚本级, 供进阶)

```
全流程开启:  管理员运行 bin\hijack_on.bat   (旧式, 同 start.bat 效果)
全流程恢复:  管理员运行 bin\hijack_off.bat  (旧式, 同 end.bat 效果)
仅补丁:     管理员运行 bin\hijack_patch.bat → py -3 src\patch_asar.py
仅代理:     py -3 scripts\hijack_daemon.py start|stop|restart|status|reload
仅客户端:   py -3 scripts\run_client.py
帧注入:     py -3 scripts\inject.py banner "文本" --sender 老师
事件监听:   py -3 scripts\watch.py
定位客户端: py -3 src\client_locator.py    /    py -3 scripts\install_info.py
```

> 注: bin 下 `start_all_silent.bat` / `stop_proxy_silent.bat` 是早期简化版,
> 现在统一用根目录 `start.bat` / `end.bat`。

## 五、各功能配置示例 (rules/hijack_rules.json)

### 改横幅内容

```json
"banner": {
  "replace": [["今晚交作业", "今晚自由活动"], ["1", "2"], ["3", "4"]],
  "remove": ["请家长签字"],
  "append": " ——教务处喵~",
  "force_sender": "校长办公室",
  "force_tts": null
}
```

- `replace`: 多组替换, **按数组顺序执行**
- `remove`: 删除子串 (先于 replace)
- `append`: 尾部追加 (最后执行)
- `force_sender`: 改发件人显示名, `""` = 不改
- `force_tts`: `true/false` 强制语音开关, `null` = 不改

TTS 朗读与横幅同字段, 文本改后语音同步变。

### 禁止横幅

```json
"banner": { "block_banner": true }
```

下行横幅帧直接丢弃, 大屏不弹。

### 禁止汉化/热更新包

```json
"block_ws_types": ["renderer.pack.push"]
```

默认**空** (不封锁), 需要时按上面的写法加。匹配的是帧的 `payload.type` (完全匹配,
以 `*` 结尾做前缀匹配如 `"desktop.update.*"`); 完整清单见 RULES_REFERENCE.md。

### 禁止客户端自动更新 (默认开启)

```json
"update": { "block": true, "block_pack": false }
```

三层拦: 服务器判定接口 (假应答 `updateAvailable:false`)、`/desktop-updates/*` 拉包 (404)、
`desktop.update.*` 推帧 (丢弃)。防的是自动更新把 asar 里的证书补丁冲掉。
想连界面热更包一起禁就 `"block_pack": true`。详见 [CONFIG_GUIDE.md](CONFIG_GUIDE.md)。

### 随机点名 / 座位规则

```json
"seat": {
  "exclude": ["张三"],
  "only": [],
  "pairs": [["小明", "小红"], ["王五", "赵六"]]
}
```

- `exclude`: 从座位表与点名名单移除 (永不显示/永不被抽)
- `only`: 只保留这些人 (空 = 所有人)
- `pairs`: 结对, 后者强制坐前者右邻位, 原右邻互换。可配多对

同时作用于: 座位表显示 (HTTP)、托盘随机点名 (students API)、班级数据推送帧 (WS)。

### 计时器

```json
"timer": { "force_seconds": 9999 }
```

`>0` 强制横幅自动关闭秒数; `0` = 不干预 (用服务器下发值)。

### 封锁 API

```json
"block_paths": ["/api/v2/xxx"]
```

命中即返回 `{"success":true,"data":{}}` 假成功, 客户端无感知。

### 低调模式

```json
"passthrough": true
```

代理照常转发, 规则全部跳过 (连接/事件仍记日志)。

## 六、帧注入与抓帧 (直接向大屏推帧)

不经教师端, 向代理里在线的教室大屏直推帧 —— 演示/验证"通道被完全控制"最直接的方式。

```bash
py -3 scripts\inject.py banner "今晚六点半自习" --sender 班主任 --seconds 60
py -3 scripts\inject.py banner "作业已发" --append " 喵~"      # 尾部追加 (传统)
py -3 scripts\inject.py banner "临时通知" --tts on          # 带语音朗读
py -3 scripts\inject.py safety "暑期安全" "不野泳, 不玩火"     # 每日安全全屏播报
py -3 scripts\inject.py teacher "期末寄语" "稳住, 能赢"        # 班主任寄语全屏
py -3 scripts\inject.py command lock_system                  # 远程控制命令
py -3 scripts\inject.py raw @frame.json                      # 原样发任意帧 (最自由)
py -3 scripts\inject.py raw - < frame.json                   # 从 stdin 读
```

- 默认原样下发; 加 `--apply-rules` 则注入帧也过规则引擎 (会被 replace/block 影响)
- `--append` 直接拼尾部文本 (与规则的 `banner.append` 独立); 想让规则统一处理就用 `--apply-rules`
- `inject.py clients` 在线会话数; `inject.py frames 50` 看最近 50 条捕获
  (含 下行/上行/注入, 带 `pass/rewrite/drop/inject` 动作标记), `--clear` 清空
- 捕获环只在内存, 长度 `debug.capture_max` (默认 200, 0 = 关闭); 长期归档用 `debug.dump_dir`
- HTTP 接口 (`POST /__inject`, `GET /__frames` 等) 见 [RULES_REFERENCE.md](RULES_REFERENCE.md)

> 注入要求"代理在线"; 没有客户端连进来时 `sent_to` 为 0。

## 七、恢复/撤销速查

| 想恢复什么 | 怎么做 |
|---|---|
| 一切恢复 | 双击 `end.bat` |
| 只暂停改写 | `passthrough: true` + reload |
| 只还原客户端 | `end.bat` (asar 自动还原); 手动: 拷 `app.asar.bak` 回去 |
| 客户端被自动更新覆盖 | 重新双击 `start.bat` (补丁自动重打, 原版备份不会被覆盖) |
| 迁移到新机器 | 整目录拷贝; 装好客户端后双击 `start.bat` |

## 八、校园网 / DNS 被封怎么办

代理**不需要你配置 DNS**。它自带多层解析兜底链, 只要有一层能用就行:

```
缓存 → 公共UDP DNS → 系统/校内 DNS → DoH(443) → 手动 IP
```

诊断、一条命令:

```bash
py -3 scripts\netcheck.py     # 看第 [3] 步系统/校内 DNS 是否有 IP
```

- 第 [3]/[4] 步有 IP 且 [5] REACHABLE → 什么都不用管, 双击 `start.bat`
- [2][3][4] 全失败 → 编辑 `rules/upstream_ip.txt`, 取消注释并填真实 IP, 再 `start.bat`
- [5] 全 BLOCKED → 校园网封了出站 443, 与本工具无关, 换网络/热点

详细分析 → [CAMPUS_NETWORK.md](CAMPUS_NETWORK.md)

## 九、故障排查入口

| 症状 | 先看 |
|---|---|
| 客户端"未连接服务器" | 完整排查 → [TROUBLESHOOTING.md](TROUBLESHOOTING.md) |
| 校园网 hosts 无效 / DNS 污染 | [CAMPUS_NETWORK.md](CAMPUS_NETWORK.md) + `netcheck.py` |
| 具体规则怎么写 | [RULES_REFERENCE.md](RULES_REFERENCE.md) / [CONFIG_GUIDE.md](CONFIG_GUIDE.md) |

## 十、课表调度 (三个弹窗按课表显示)

```powershell
py -3 scripts\schedule.py import timetable.json   # 导入课表 (自动开启调度)
py -3 scripts\schedule.py status                  # 上课中/课间/课表外 + 折叠队列
py -3 scripts\schedule.py force class             # 演练: 强制"上课中"
```

三个弹窗 (横幅 banner / 小弹窗 popup / 全屏 fullscreen) 的策略写在
`rules/hijack_rules.json` 的 `schedule.targets`。`break_only` 就是"课间显示、课上不显示";
`blocked_action: "fold"` 时课上被压下的弹窗会在课间自动补发 (一条不丢)。
课表格式、字段逐条说明与常用配方 → [CONFIG_GUIDE.md](CONFIG_GUIDE.md)。
