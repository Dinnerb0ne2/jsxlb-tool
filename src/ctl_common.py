# -*- coding: utf-8 -*-
"""start/end 一键流程的共享操作 (hosts / CA / 进程 / asar)。

系统写操作集中于此, 由 start.bat / end.bat 提权后调用。
"""
import os
import sys
import time
import glob
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
    sys.path.insert(0, BIN)
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
        attrs = 0x1 if ro else 0x0
        if ro:
            ctypes.windll.kernel32.SetFileAttributesW(path, 0x1)
        else:
            ctypes.windll.kernel32.SetFileAttributesW(path, 0x80)
    except Exception:
        pass

def asar_patched(d):
    """True if current asar differs from backup (i.e. already patched)."""
    asar = os.path.join(d, "resources", "app.asar")
    bak = asar + ".bak"
    if not os.path.isfile(asar):
        return False
    if not os.path.isfile(bak):
        return False
    return os.path.getsize(asar) != os.path.getsize(bak)

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

def wait_port_free(port, timeout=15):
    for _ in range(timeout):
        try:
            s = socket.create_connection(("127.0.0.1", port), timeout=1)
            s.close()          # still accepting = occupied
        except OSError:
            return True
        time.sleep(1)
    return False

def kill_port_owners(ports=(443, 8100)):
    """Kill any process LISTENING on the given ports (orphan proxies).
    Returns list of killed PIDs. Needs admin (start/end run elevated)."""
    killed = []
    try:
        out = subprocess.run(["netstat", "-ano"], capture_output=True, text=True,
                             errors="replace", timeout=20).stdout
    except Exception:
        return killed
    pids = set()
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 5 and "LISTENING" in line:
            lp = parts[1]
            for port in ports:
                if lp.endswith(":%d" % port) and parts[-1].isdigit():
                    pids.add(int(parts[-1]))
    for pid in pids:
        if pid <= 4:
            continue
        run(["taskkill", "/F", "/PID", str(pid)])
        killed.append(pid)
    return killed
