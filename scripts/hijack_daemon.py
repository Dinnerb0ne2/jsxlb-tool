# -*- coding: utf-8 -*-
"""代理进程控制器。

  start|stop|restart|status|reload
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))), "src"))
import oplog
import os, sys, subprocess, glob, json, urllib.request, socket, time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROXY = os.path.join(ROOT, "src", "hijack_proxy.py")
PIDFILE = os.path.join(ROOT, "logs", "hijack_proxy.pid")
LOGFILE = os.path.join(ROOT, "logs", "hijack_proxy.log")
RULES = os.path.join(ROOT, "rules", "hijack_rules.json")

DETACHED = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP

def read_pid():
    try:
        return int(open(PIDFILE).read().strip())
    except Exception:
        return None

def pid_alive(pid):
    if not pid:
        return False
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if handle:
            kernel32.CloseHandle(handle)
            return True
        return False
    except Exception:
        return False

def is_our_proxy(pid):
    """Verify the PID is really our proxy before killing it.
    双重验证: (a) 命令行含 hijack_proxy.py; (b) 该 PID 监听 443/8100。
    (b) 兜底: elevated 进程拿不到命令行时用端口归属判断。"""
    if not pid:
        return False
    # (a) 命令行验证
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             f"(Get-CimInstance Win32_Process -Filter \"ProcessId={pid}\").CommandLine"],
            capture_output=True, text=True, timeout=15, errors="replace").stdout
        if "hijack_proxy.py" in (out or ""):
            return True
    except Exception:
        pass
    # (b) 端口归属验证 (PID 监听 443 或 8100 = 是我们的代理)
    try:
        out = subprocess.run(["netstat", "-ano"], capture_output=True, text=True,
                             timeout=15, errors="replace").stdout
        for line in out.splitlines():
            parts = line.split()
            if len(parts) >= 4 and parts[-1] == str(pid) and "LISTENING" in line:
                if parts[1].endswith(":443") or parts[1].endswith(":8100"):
                    return True
    except Exception:
        pass
    return False

def wait_ports_free(timeout=15):
    """等 443/8100 释放 (上一实例 socket 收尾), 防 bind 10048 竞态"""
    for _ in range(timeout):
        busy = False
        try:
            s = socket.create_connection(("127.0.0.1", 8100), timeout=1)
            s.close(); busy = True
        except OSError:
            pass
        try:
            s = socket.create_connection(("127.0.0.1", 443), timeout=1)
            s.close(); busy = True
        except OSError:
            pass
        if not busy:
            return True
        time.sleep(1)
    return False

def start():
    if pid_alive(read_pid()) and is_our_proxy(read_pid()):
        print("[=] proxy already running (PID %d)" % read_pid())
        return
    # clean stale pidfile
    try:
        os.remove(PIDFILE)
    except OSError:
        pass

    # 等上一实例端口完全释放 (防 bind 竞态)
    if not wait_ports_free():
        print("[!] 端口 443/8100 仍被占用, 启动中止。用 netstat -ano | findstr :443 找占用者")
        return

    python = sys.executable
    # build command:  python hijack_proxy.py --host 0.0.0.0 --tls-port 443 --http-port 8100
    cmd = [python, PROXY, "--host", "0.0.0.0", "--tls-port", "443", "--http-port", "8100"]
    # 追加模式: 保留历史日志 (重启前的连接/改写记录可用于事后诊断)
    logf = open(LOGFILE, "a", encoding="utf-8", errors="replace")
    logf.write("\n---- proxy start %s ----\n" % time.strftime("%Y-%m-%d %H:%M:%S"))
    logf.flush()
    p = subprocess.Popen(cmd, stdout=logf, stderr=subprocess.STDOUT,
                         cwd=ROOT, creationflags=DETACHED, close_fds=True)
    with open(PIDFILE, "w") as f:
        f.write(str(p.pid))
    print("[+] proxy started silently (PID %d), log: %s" % (p.pid, os.path.normpath(LOGFILE)))

def stop():
    pid = read_pid()
    if not pid_alive(pid) or not is_our_proxy(pid):
        print("[=] proxy not running")
        try:
            os.remove(PIDFILE)
        except OSError:
            pass
        return
    # 直接强杀: 代理无持久状态可丢, 端口释放由 start 前的 wait_ports_free 兜底
    subprocess.run(["taskkill", "/F", "/PID", str(pid)],
                   capture_output=True)
    try:
        os.remove(PIDFILE)
    except OSError:
        pass
    print("[+] proxy stopped (PID %d)" % pid)

def status():
    pid = read_pid()
    alive = pid_alive(pid) and is_our_proxy(pid)
    print("proxy:", "RUNNING (PID %d)" % pid if alive else "STOPPED")
    if alive:
        try:
            s = json.load(urllib.request.urlopen("http://127.0.0.1:8100/__status", timeout=5))
            print("clients: %d  capture: %d  upstream: %s"
                  % (s.get("clients", 0), s.get("capture", 0), s.get("upstreamIp") or "?"))
        except Exception:
            pass
        try:
            r = json.load(urllib.request.urlopen("http://127.0.0.1:8100/__rules", timeout=5))
            print("rules: passthrough=%s  replace=%d  pairs=%d  append=%r"
                  % (r.get("passthrough"), len(r["banner"]["replace"]),
                     len(r["seat"]["pairs"]), r["banner"]["append"]))
        except Exception as e:
            print("rules API unreachable:", e)
    # last few rewrite events
    try:
        lines = open(LOGFILE, encoding="utf-8", errors="replace").read().splitlines()
        events = [l for l in lines if any(k in l for k in
                  ("BANNER", "DROPPED", "TIMER", "SEAT", "STUDENTS", "BLOCK", "INJECT",
                   "RULES updated", "client connected"))]
        print("--- last events ---")
        for l in events[-6:]:
            print(" ", l)
    except OSError:
        pass

def reload():
    rules = json.load(open(RULES, encoding="utf-8"))
    req = urllib.request.Request("http://127.0.0.1:8100/__rules",
                                 data=json.dumps(rules, ensure_ascii=False).encode("utf-8"),
                                 headers={"Content-Type": "application/json"}, method="POST")
    r = json.load(urllib.request.urlopen(req, timeout=8))
    print("[+] rules reloaded: replace=%d  pairs=%d  append=%r  passthrough=%s"
          % (len(r["banner"]["replace"]), len(r["seat"]["pairs"]),
             r["banner"]["append"], r.get("passthrough")))

def restart():
    stop()
    wait_ports_free()
    start()

def main():
    action = sys.argv[1] if len(sys.argv) > 1 else "status"
    oplog.op(action)
    {"start": start, "stop": stop, "restart": restart,
     "status": status, "reload": reload}.get(action, status)()

if __name__ == "__main__":
    main()
