# -*- coding: utf-8 -*-
"""网络环境诊断 (hosts / DNS 污染 / 出站 / 代理)。

六步: 本机解析, 公共UDP DNS, 系统DNS, DoH, 候选IP证书验证, 本机代理。
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))), "src"))
import oplog
import os
import ssl
import sys
import socket
import struct
import json
import random

DOMAIN = "xlb.810086.com"
FALLBACK_IP = "8.134.221.255"   # 已知历史 IP (DNS 全挂时兜底测试用)

def raw_dns(domain, server, timeout=4):
    tid = random.randint(0, 65535)
    q = struct.pack(">HHHHHH", tid, 0x0100, 1, 0, 0, 0)
    for part in domain.split("."):
        q += bytes([len(part)]) + part.encode()
    q += b"\x00" + struct.pack(">HH", 1, 1)
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(timeout)
    try:
        s.sendto(q, (server, 53))
        data, _ = s.recvfrom(512)
    finally:
        s.close()
    ancount = struct.unpack(">H", data[6:8])[0]
    i = 12
    while data[i] != 0:
        i += data[i] + 1
    i += 5
    ips = []
    for _ in range(ancount):
        if data[i] & 0xC0 == 0xC0:
            i += 2
        else:
            while data[i] != 0:
                i += data[i] + 1
            i += 1
        rtype, _, _, rdlen = struct.unpack(">HHIH", data[i:i + 10])
        i += 10
        if rtype == 1 and rdlen == 4:
            ips.append(".".join(str(b) for b in data[i:i + 4]))
        i += rdlen
    return ips

def system_dns(domain):
    try:
        return sorted({sa[0] for _, _, _, _, sa in socket.getaddrinfo(domain, 443, socket.AF_INET)})
    except Exception as e:
        return ["FAIL: %s" % e]

def tcp(ip, port=443, timeout=5):
    try:
        s = socket.create_connection((ip, port), timeout=timeout)
        s.close()
        return True
    except Exception:
        return False

def is_bad_ip(ip):
    try:
        p = [int(x) for x in ip.split(".")]
        if len(p) != 4:
            return True
    except Exception:
        return True
    a, b = p[0], p[1]
    if a == 0 or a == 127 or a >= 224 or a == 10:
        return True
    if a == 172 and 16 <= b <= 31:
        return True
    if a == 192 and b == 168:
        return True
    if a == 169 and b == 254:
        return True
    if a == 100 and 64 <= b <= 127:
        return True
    return False

def tls_verify(ip, domain="xlb.810086.com", timeout=6):
    """TLS + 系统CA + 域名匹配 —— 抗污染核心。返回 (ok, info)"""
    try:
        ctx = ssl.create_default_context()
        s = socket.create_connection((ip, 443), timeout=timeout)
        ss = ctx.wrap_socket(s, server_hostname=domain)
        c = ss.getpeercert()
        iss = c.get("issuer", ())
        cn = next((v for rdn in iss for k, v in rdn if k == "commonName"), "?")
        ss.close()
        return True, "CA-verified (issuer CN=%s)" % cn
    except ssl.SSLCertVerificationError as e:
        return False, "证书不匹配 (%s)" % str(e)[:36]
    except Exception as e:
        return False, "TLS失败 (%s)" % type(e).__name__

def doh_ip(domain, ip, host, path, timeout=6):
    """DoH over 固定IP + SNI (不依赖域名解析, 抗污染)"""
    try:
        ctx = ssl.create_default_context()
        s = socket.create_connection((ip, 443), timeout=timeout)
        ss = ctx.wrap_socket(s, server_hostname=host)
        ss.sendall(("GET %s?name=%s&type=A HTTP/1.1\r\nHost: %s\r\n"
                    "Accept: application/dns-json\r\nConnection: close\r\n\r\n"
                    % (path, domain, host)).encode())
        data = b""
        while True:
            c = ss.recv(4096)
            if not c:
                break
            data += c
        ss.close()
        j = json.loads(data.split(b"\r\n\r\n", 1)[1].decode("utf-8", "replace"))
        return [a["data"] for a in (j.get("Answer") or []) if a.get("type") == 1]
    except Exception as e:
        return ["FAIL: %s" % type(e).__name__]

def main():
    print("=== [1] hosts/系统解析 (客户端视角) ===")
    try:
        ip = socket.gethostbyname(DOMAIN)
        print("   %s -> %s   [%s]" % (DOMAIN, ip,
              "OK 劫持生效" if ip == "127.0.0.1" else "!! hosts 未生效"))
    except Exception as e:
        print("   FAIL:", e)

    print("=== [2] 公共 UDP DNS (代理防自环用; 校园网常封) ===")
    pub = set()
    for srv in ("223.5.5.5", "119.29.29.29", "8.8.8.8"):
        try:
            ips = raw_dns(DOMAIN, srv)
            print("   %-15s -> %s" % (srv, ips or "(空)"))
            pub.update(ips)
        except Exception as e:
            print("   %-15s -> FAIL (%s)" % (srv, type(e).__name__))
    if not pub:
        print("   [!] 公共 UDP DNS 全失败 -> 校园网常见; 不致命, 看 [3][4]")

    print("=== [3] 系统/校内 DNS (校园网主力兜底) ===")
    sysips = system_dns(DOMAIN)
    print("   ->", sysips)
    real_sys = [i for i in sysips if i and not i.startswith("FAIL") and i != "127.0.0.1"]

    print("=== [4] DoH (固定IP直连 + SNI, 抗污染) ===")
    dohips = doh_ip(DOMAIN, "223.5.5.5", "dns.alidns.com", "/resolve")
    print("   223.5.5.5 (dns.alidns.com) ->", dohips)
    if all(str(x).startswith("FAIL") for x in dohips):
        dohips = doh_ip(DOMAIN, "119.29.29.29", "doh.pub", "/dns-query")
        print("   119.29.29.29 (doh.pub)     ->", dohips)
    real_doh = [i for i in dohips if not str(i).startswith("FAIL")]

    print("=== [5] 候选 IP 验证: 保留地址过滤 + TLS 证书 (抗污染) ===")
    cands = []
    for i in list(pub) + real_sys + real_doh + [FALLBACK_IP]:
        if i and i not in cands:
            cands.append(i)
    any_ok = False
    clean = None
    for ip in cands:
        if is_bad_ip(ip):
            print("   %-16s -> 剔除 (私有/保留地址, 疑似DNS污染)" % ip)
            continue
        vok, vinfo = tls_verify(ip)
        reach = tcp(ip)
        any_ok = any_ok or reach
        if vok and clean is None:
            clean = ip
        print("   %-16s -> %s | %s" % (ip, "REACHABLE" if reach else "BLOCKED", vinfo))
    if clean:
        print("   [OK] 真服务器 (证书验证通过): %s" % clean)
    elif any_ok:
        print("   [!] 无 IP 通过证书验证 (疑似严重污染/证书变更); 代理仍会尝试可用 IP")
    else:
        print("   [!] 全部不可达: 校园网封了到该服务器的出站 443 (DNS 无关)")
        print("       -> 代理无法回源, 换网络/热点才能用 (劫持本身没问题)")

    print("=== [6] 本机代理 443 ===")
    proxy_up = tcp("127.0.0.1")
    print("   127.0.0.1:443 -> %s" % ("REACHABLE" if proxy_up else "NOT LISTENING"))
    if not proxy_up:
        print("   -> 双击 start.bat")

    print()
    print("=== 结论与处置 ===")
    if clean:
        print("  [OK] 已验证真服务器: %s —— 凭代理直接双击 start.bat 即可" % clean)
    elif pub or real_sys or real_doh:
        print("  DNS 可用层: %s" % (("公共UDP " if pub else "") +
              ("系统/校内 " if real_sys else "") + ("DoH" if real_doh else "")))
        print("  但无 IP 通过证书验证 —— 疑似严重污染或上游证书变更。")
        print("  处置: 在 rules/upstream_ip.txt 写真实 IP; 或换网络验证真实 IP。")
    else:
        print("  所有 DNS 层都不通 -> 手动指定 IP:")
        print("    1) 编辑 rules/upstream_ip.txt, 写入一行真实 IP (如 %s)" % FALLBACK_IP)
        print("    2) 或启动参数 --upstream-ip %s" % FALLBACK_IP)
        print("    3) 然后双击 start.bat")
    print("  代理抗污染说明: 即使 DNS 返回假 IP, 证书验证也会自动识别并剔除,")
    print("                并回退到下一层解析; 全部失败才需手动指定。")
    if any_ok is False:
        print("  [注意] 即使指定 IP, 443 也不可达 -> 是校园网出站封锁, 非本工具问题。")

if __name__ == "__main__":
    oplog.op("run", oplog.run_arg())
    main()
