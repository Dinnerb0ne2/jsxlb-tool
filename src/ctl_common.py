# -*- coding: utf-8 -*-
"""start/end 一键流程的共享操作 (hosts / CA / 进程 / asar)。

系统写操作集中于此, 由 start.bat / end.bat 提权后调用。
"""
import os
import sys
import time
import glob
import json
import shutil
import socket
import subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SRC = HERE
SCRIPTS = os.path.join(ROOT, "scripts")
LOG = os.path.join(ROOT, "logs", "start_end.log")

def log(msg, also_print=True):
    line = "[%s] %s" % (time.strftime("%H:%M:%S"), msg)
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass
    if also_print:
        print(line, flush=True)

def run(cmd, **kw):
    """Run command, return CompletedProcess. Never raise."""
    kw.setdefault("capture_output", True)
    kw.setdefault("text", True)
    kw.setdefault("errors", "replace")
    kw.setdefault("timeout", 120)
    try:
        return subprocess.run(cmd, **kw)
    except Exception as e:
        return None

def client_dir():
    """Locate client install dir (same logic as client_locator)."""
    sys.path.insert(0, SRC)
    try:
        from client_locator import locate
        d = locate()
        if d:
            return d
    except Exception as e:
        log("[!] locator error: %s" % e)
    return None

def client_exe(d):
    for e in glob.glob(os.path.join(d, "*.exe")):
        if "uninstall" not in os.path.basename(e).lower():
            return e
    return None

def kill_client():
    """Kill ALL classroom-client processes (by path). Handles Chinese exe name safely."""
    ps = "Get-Process | Where-Object {$_.Path -like '*jsxlb*'} | Select-Object -ExpandProperty Id"
    r = run(["powershell", "-NoProfile", "-Command", ps])
    killed = 0
    if r and r.stdout:
        for pid in r.stdout.split():
            pid = pid.strip()
            if pid.isdigit():
                run(["taskkill", "/F", "/PID", pid])
                killed += 1
    return killed

def set_readonly(path, ro):
    try:
        import ctypes
        if ro:
            ctypes.windll.kernel32.SetFileAttributesW(path, 0x1)
        else:
            ctypes.windll.kernel32.SetFileAttributesW(path, 0x80)
    except Exception:
        pass

def asar_patched(d):
    """已补丁 = main.js 区间内无 `rejectUnauthorized: true`。

    不能用大小对比: 补丁是等长字节替换 (24B -> 24B), 原版与补丁版大小完全相同。
    """
    asar = os.path.join(d, "resources", "app.asar")
    if not os.path.isfile(asar):
        return False
    try:
        import struct
        with open(asar, "rb") as f:
            u = struct.unpack("<4I", f.read(16))
            json_size = u[3]
            base = 8 + u[1]
            hdr = json.loads(f.read(json_size).decode("utf-8"))
        mj = hdr["files"]["main.js"]
        off = base + int(mj["offset"])
        size = int(mj["size"])
        with open(asar, "rb") as f:
            f.seek(off)
            chunk = f.read(size)
        return b"rejectUnauthorized: true" not in chunk
    except Exception as e:
        print("[!] asar check failed: %s" % e, file=sys.stderr)
        return False


def hosts_has():
    try:
        with open(r"C:\Windows\System32\drivers\etc\hosts", encoding="ascii", errors="ignore") as f:
            return "xlb.810086.com" in f.read()
    except OSError:
        return False

def hosts_add():
    try:
        with open(r"C:\Windows\System32\drivers\etc\hosts", "a", encoding="ascii") as f:
            f.write("\n127.0.0.1 xlb.810086.com\n")
        run(["ipconfig", "/flushdns"])
        return True
    except OSError as e:
        log("[!] hosts add failed: %s" % e)
        return False

def hosts_remove():
    try:
        p = r"C:\Windows\System32\drivers\etc\hosts"
        with open(p, encoding="ascii", errors="ignore") as f:
            lines = f.readlines()
        with open(p, "w", encoding="ascii") as f:
            for ln in lines:
                if "xlb.810086.com" not in ln:
                    f.write(ln)
        run(["ipconfig", "/flushdns"])
        return True
    except OSError as e:
        log("[!] hosts remove failed: %s" % e)
        return False

def ca_present():
    r = run(["certutil", "-store", "Root", "xlb-proxy-root-ca"])
    return bool(r and r.returncode == 0)

def ca_add():
    ca = os.path.join(ROOT, "backup", "hijack_ca.pem")
    if not os.path.isfile(ca):
        log("[!] CA file missing: %s (run proxy once to generate)" % ca)
        return False
    r = run(["certutil", "-addstore", "-f", "Root", ca])
    return bool(r and r.returncode == 0)

def ca_remove():
    run(["certutil", "-delstore", "Root", "xlb-proxy-root-ca"])

def upstream_ip_override():
    """读 rules/upstream_ip.txt 里的真实 IP (校园网 DNS 被封时的手动兜底)。
    有值则通过环境变量 XLB_UPSTREAM_IP 传给代理子进程。返回 IP 或空串。"""
    f = os.path.join(ROOT, "rules", "upstream_ip.txt")
    try:
        if os.path.isfile(f):
            for line in open(f, encoding="utf-8", errors="ignore"):
                line = line.strip()
                if line and not line.startswith("#"):
                    return line
    except OSError:
        pass
    return ""

def daemon(cmd, extra_env=None):
    env = os.environ.copy()
    if extra_env:
        env.update(extra_env)
    return run([sys.executable, os.path.join(SCRIPTS, "hijack_daemon.py"), cmd], env=env)

def port_open(port, timeout=1):
    """端口可连 = 有人在监听"""
    try:
        s = socket.create_connection(("127.0.0.1", port), timeout=timeout)
        s.close()
        return True
    except OSError:
        return False

def wait_port_free(port, timeout=15):
    for _ in range(timeout):
        if not port_open(port):
            return True
        time.sleep(1)
    return False

def wait_port_open(port, timeout=15):
    """等端口监听就绪 (真实探活, 用于验证代理是否真的起来了)"""
    for _ in range(timeout):
        if port_open(port):
            return True
        time.sleep(1)
    return False

def _is_foreign_listener(pid):
    """True = 确定不是我们的代理 (命令行既无 hijack_proxy 也无 python)。
    取不到命令行时返回 False (提权/权限限制场景, 保持旧行为按代理处理)。"""
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             f"(Get-CimInstance Win32_Process -Filter \"ProcessId={pid}\").CommandLine"],
            capture_output=True, text=True, timeout=10, errors="replace").stdout or ""
    except Exception:
        return False
    out = out.strip().lower()
    if not out:
        return False
    return ("hijack_proxy" not in out) and ("python" not in out)

def _listener_pids(ports):
    """从 netstat 反查监听这些端口的 PID。"""
    pids = set()
    try:
        out = subprocess.run(["netstat", "-ano"], capture_output=True, text=True,
                             errors="replace", timeout=20).stdout
    except Exception:
        return pids
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 5 and "LISTENING" in line:
            lp = parts[1]
            for port in ports:
                if lp.endswith(":%d" % port) and parts[-1].isdigit():
                    pids.add(int(parts[-1]))
    return pids

def kill_port_owners(ports=(443, 8100)):
    """Kill our orphan proxy processes LISTENING on the given ports.
    安全阀: 命令行明确不是代理的进程只警告不杀; 杀完**复查**——还在监听就如实报到
    (提权代理在非提权下杀不掉), 不再假装成功。
    Returns list of killed PIDs. Needs admin (start/end run elevated)."""
    killed = []
    for pid in sorted(_listener_pids(ports)):
        if pid <= 4:
            continue
        if _is_foreign_listener(pid):
            log("[!] 端口 %s 被非代理进程占用 (PID %d), 跳过不杀" % (list(ports), pid))
            continue
        run(["taskkill", "/F", "/PID", str(pid)])
        time.sleep(0.3)
        if pid in _listener_pids(ports):
            log("[!] 无法终止 PID %d (提权进程, 需管理员运行)" % pid)
            continue
        killed.append(pid)
    return killed
