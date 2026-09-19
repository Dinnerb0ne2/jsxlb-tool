# -*- coding: utf-8 -*-
"""定位教室客户端安装目录。

顺序: 环境变量 XLB_CLIENT_DIR > 注册表 > 常见路径 > 快捷方式 > 盘符扫描。
判定: 目录含 resources/app.asar 且有非 Uninstall 的 exe。
"""
import os
import sys
import glob
import re
import subprocess

def _run(cmd, timeout=15):
    """subprocess 安全执行: errors=replace 防 GBK 解码崩溃"""
    return subprocess.run(cmd, capture_output=True, text=True,
                          errors="replace", timeout=timeout).stdout

def _non_uninstall_exe(d):
    for exe in glob.glob(os.path.join(d, "*.exe")):
        if "uninstall" not in os.path.basename(exe).lower():
            return exe
    return None


def _looks_like_jsxlb(d):
    """特征校验: 避免误匹配其他 Electron 应用 (aDrive / Cloudflare 等)。
    满足任一: 目录/exe 名含关键词, 或 app-update.yml / package.json 指向 810086。"""
    keys = ("jsxlb", "教室", "小喇叭")
    name = os.path.basename(d.rstrip("\\/")).lower()
    if any(k in name for k in keys):
        return True
    exe = _non_uninstall_exe(d)
    if exe and any(k in os.path.basename(exe).lower() for k in keys):
        return True
    for f in ("app-update.yml", "package.json"):
        p = os.path.join(d, "resources", f)
        if os.path.isfile(p):
            try:
                head = open(p, "rb").read(8192)
            except OSError:
                continue
            if b"810086" in head or b"jsxlb" in head.lower():
                return True
    return False


def _is_client_dir(d):
    """目录是否为教室小喇叭安装目录: asar + 非卸载器 exe + 特征校验。"""
    if not d or not os.path.isdir(d):
        return False
    if not os.path.isfile(os.path.join(d, "resources", "app.asar")):
        return False
    if not _non_uninstall_exe(d):
        return False
    return _looks_like_jsxlb(d)


def _pick_exe(d):
    """从安装目录挑主程序 exe (排除 Uninstall/卸载器)"""
    return _non_uninstall_exe(d)


def _from_env():
    """显式指定 = 用户确认, 只做基本校验 (asar + exe), 不要求特征。"""
    d = os.environ.get("XLB_CLIENT_DIR", "").strip()
    if d and os.path.isdir(d) \
       and os.path.isfile(os.path.join(d, "resources", "app.asar")) \
       and _non_uninstall_exe(d):
        return d
    return None

def _from_registry():
    """查 NSIS/electron-builder 卸载注册表项 (HKLM/HKCU, 32/64 位树)"""
    keys = [
        r"HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall",
        r"HKLM\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall",
        r"HKCU\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall",
    ]
    for key in keys:
        try:
            out = _run(["reg", "query", key, "/s", "/f", "jsxlb", "/d"])
            # reg 搜索命中行形如:  HKEY_...\jsxlb    InstallLocation    REG_SZ    C:\Program Files\jsxlb
            for line in out.splitlines():
                line = line.strip()
                if "InstallLocation" in line and "REG_SZ" in line:
                    d = line.split("REG_SZ")[-1].strip()
                    if _is_client_dir(d):
                        return d
                # 回退: 键名本身像安装目录的
                if line.startswith("HKEY_") and ("jsxlb" in line.lower() or "810086" in line):
                    # 读取该键所有值
                    try:
                        detail = _run(["reg", "query", line])
                        for dl in detail.splitlines():
                            if "InstallLocation" in dl and "REG_SZ" in dl:
                                d = dl.split("REG_SZ")[-1].strip()
                                if _is_client_dir(d):
                                    return d
                            if "DisplayIcon" in dl and "REG_SZ" in dl:
                                icon = dl.split("REG_SZ")[-1].strip().split(",")[0]
                                d = os.path.dirname(icon)
                                if _is_client_dir(d):
                                    return d
                    except Exception:
                        pass
        except Exception:
            continue
    # DisplayName 中文匹配 ("教室小喇叭")
    for key in keys:
        try:
            out = _run(["reg", "query", key, "/s", "/f", "教室", "/d"])
            for line in out.splitlines():
                line = line.strip()
                if line.startswith("HKEY_"):
                    try:
                        detail = _run(["reg", "query", line])
                        for dl in detail.splitlines():
                            if "InstallLocation" in dl and "REG_SZ" in dl:
                                d = dl.split("REG_SZ")[-1].strip()
                                if _is_client_dir(d):
                                    return d
                            if "DisplayIcon" in dl and "REG_SZ" in dl:
                                icon = dl.split("REG_SZ")[-1].strip().split(",")[0]
                                d = os.path.dirname(icon)
                                if _is_client_dir(d):
                                    return d
                    except Exception:
                        pass
        except Exception:
            continue
    return None

def _candidate_dirs():
    """常见安装位置"""
    candidates = []
    pf = os.environ.get("ProgramFiles", r"C:\Program Files")
    pf86 = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
    lad = os.environ.get("LOCALAPPDATA", "")
    candidates += [
        os.path.join(pf, "jsxlb"),
        os.path.join(pf86, "jsxlb"),
        os.path.join(lad, "Programs", "jsxlb") if lad else "",
        r"C:\jsxlb",
        r"D:\jsxlb",
    ]
    # 任何盘符根下的 jsxlb
    for letter in "CDEFGH":
        candidates.append(r"%s:\jsxlb" % letter)
    # 上面这些目录里含版本号子目录的情况扫一层
    out = []
    for c in candidates:
        if not c:
            continue
        if _is_client_dir(c):
            out.append(c)
        # jsxlb\教室小喇叭x.x.x / jsxlb\app-1.2.1 之类
        try:
            for sub in os.listdir(c):
                sd = os.path.join(c, sub)
                if os.path.isdir(sd) and _is_client_dir(sd):
                    out.append(sd)
        except OSError:
            pass
    return out

def _from_shortcuts():
    """解析开始菜单/桌面快捷方式 (.lnk) 指向的 exe"""
    import struct
    targets = []
    search_roots = [
        os.path.join(os.environ.get("ProgramData", r"C:\ProgramData"),
                     "Microsoft", "Windows", "Start Menu"),
        os.path.join(os.environ.get("APPDATA", ""),
                     "Microsoft", "Windows", "Start Menu") if os.environ.get("APPDATA") else "",
        os.path.join(os.environ.get("PUBLIC", r"C:\Users\Public"), "Desktop"),
        os.path.join(os.environ.get("USERPROFILE", ""), "Desktop") if os.environ.get("USERPROFILE") else "",
    ]
    lnks = []
    for root in search_roots:
        if root and os.path.isdir(root):
            lnks += glob.glob(os.path.join(root, "**", "*.lnk"), recursive=True)
    # 只解析名字带教室/小喇叭/喇叭/jsxlb 的 lnk, 避免乱解析
    for lnk in lnks:
        name = os.path.basename(lnk)
        if not any(k in name for k in ("教室", "小喇叭", "喇叭", "jsxlb", "xlb")):
            continue
        try:
            # 纯 python 解析 .lnk 的本地路径 (不依赖 win32com)
            raw = open(lnk, "rb").read()
            if b"C:\\" in raw or b"D:\\" in raw or b"E:\\" in raw:
                # 粗提取: 找 ASCII 路径段
                import re
                m = re.search(rb"([A-Z]:\\[^\x00\r\n]+?\.exe)", raw)
                if m:
                    targets.append(m.group(1).decode("gbk", errors="ignore"))
        except Exception:
            continue
    for exe in targets:
        d = os.path.dirname(exe)
        if _is_client_dir(d):
            return d
        # exe 可能在 app-x.y.z 子目录, 安装目录在其上层
        parent = os.path.dirname(d)
        if _is_client_dir(parent):
            return parent
    return None

def _from_drive_scan():
    """最后手段: 扫各盘 (限两级深) 找 resources/app.asar + exe 的组合"""
    drives = ["%s:\\" % c for c in "CDEFGH" if os.path.isdir("%s:\\" % c)]
    scan_roots = []
    for d in drives:
        scan_roots.append(d)
        scan_roots += [os.path.join(d, x) for x in
                       ("Program Files", "Program Files (x86)", "Programs")]
        lad = os.environ.get("LOCALAPPDATA", "")
        if lad:
            scan_roots.append(os.path.join(lad, "Programs"))
    seen = set()
    for root in scan_roots:
        if not os.path.isdir(root) or root in seen:
            continue
        seen.add(root)
        try:
            for entry in os.listdir(root):
                sub = os.path.join(root, entry)
                if not os.path.isdir(sub):
                    continue
                if _is_client_dir(sub):
                    return sub
                # 三级: sub 下找
                try:
                    for e2 in os.listdir(sub):
                        sub2 = os.path.join(sub, e2)
                        if os.path.isdir(sub2) and _is_client_dir(sub2):
                            return sub2
                except OSError:
                    pass
        except OSError:
            continue
    return None

def locate():
    """返回安装目录, 找不到返回 None"""
    for finder in (_from_env, _from_registry, _candidate_dirs, _from_shortcuts, _from_drive_scan):
        try:
            d = finder()
        except Exception:
            d = None
        if d:
            if isinstance(d, list):
                d = d[0]
            return os.path.normpath(d)
    return None

def locate_or_die():
    d = locate()
    if not d:
        print("[!] 无法自动找到教室小喇叭安装目录。")
        print("    解决: 设置环境变量后重试, 例如:")
        print('          set XLB_CLIENT_DIR=C:\\Program Files\\jsxlb')
        sys.exit(1)
    return d

if __name__ == "__main__":
    d = locate()
    print(d if d else "[!] not found")
