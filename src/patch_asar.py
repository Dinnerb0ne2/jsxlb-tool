# -*- coding: utf-8 -*-
"""客户端 asar 证书校验补丁。

主路径: main.js 的 `rejectUnauthorized: true` 与 `rejectUnauthorized:false`
长度相同 (24B), 直接字节替换 + 更新 integrity, 毫秒级, 无需 Node.js。
回退: 找不到时用 npx @electron/asar 解包重打包。原版备份 app.asar.bak。
"""
import oplog
import os
import sys
import json
import time
import shutil
import struct
import hashlib

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

NEEDLE = b"rejectUnauthorized: true"
REPLACEMENT = b"rejectUnauthorized:false"   # 等长 (24 字节), 不要改动空格

def find_client():
    from client_locator import locate
    return locate()

def parse_asar_header(data):
    """返回 (header_dict, json_size, base_offset)。asar 头布局:
    [u32=4][u32=headerPickleSize][u32=headerStringSize][u32=headerJsonSize][headerJson...]
    数据区起点 = 8 + headerPickleSize
    """
    u = struct.unpack("<4I", bytes(data[:16]))
    json_size = u[3]
    base = 8 + u[1]
    header = json.loads(bytes(data[16:16 + json_size]).decode("utf-8"))
    return header, json_size, base

def patch_fast(asar_path):
    """直接字节替换 main.js 区间 + 更新 integrity。返回替换处数 (0 = 无需/未找到)。"""
    with open(asar_path, "rb") as f:
        data = bytearray(f.read())

    header, json_size, base = parse_asar_header(data)
    entry = (header.get("files") or {}).get("main.js")
    if not entry or "offset" not in entry or "size" not in entry:
        return 0, "main.js entry not found in asar header"

    off = base + int(entry["offset"])
    size = int(entry["size"])
    if off < 0 or size <= 0 or off + size > len(data):
        return 0, "main.js range out of bounds"

    chunk = bytes(data[off:off + size])
    hits = chunk.count(NEEDLE)
    if hits == 0:
        # 已经是补丁版, 或写法不同 -> 交给上层判断
        return 0, "needle not found in main.js (already patched or different source)"

    new_chunk = chunk.replace(NEEDLE, REPLACEMENT)
    assert len(new_chunk) == len(chunk), "replacement must be same length"
    data[off:off + size] = new_chunk

    # 更新 main.js 的 integrity SHA256 (等长替换, 不破坏头部结构)
    integ = (entry.get("integrity") or {})
    old_hash = integ.get("hash")
    if old_hash:
        new_hash = hashlib.sha256(new_chunk).hexdigest()
        hdr_bytes = bytes(data[16:16 + json_size])
        if old_hash.encode() in hdr_bytes:
            pos = hdr_bytes.index(old_hash.encode())
            data[16 + pos:16 + pos + len(old_hash)] = new_hash.encode()
        # 分块 hash 列表 (小块文件通常单块)
        blocks = integ.get("blocks")
        if isinstance(blocks, list) and len(blocks) == 1:
            hdr_bytes = bytes(data[16:16 + json_size])
            if blocks[0].encode() in hdr_bytes:
                pos = hdr_bytes.index(blocks[0].encode())
                data[16 + pos:16 + pos + len(blocks[0])] = new_hash.encode()

    tmp = asar_path + ".tmp"
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, asar_path)
    return hits, "fast replace OK"

def patch_via_repackage(asar_path):
    """回退: npx @electron/asar 解包->改->重打包 (需要 Node.js)。"""
    tmp = os.path.join(HERE, "..", "tmp")
    work = os.path.join(tmp, "work")
    out = os.path.join(tmp, "out.asar")
    os.makedirs(tmp, exist_ok=True)
    for p in (work, out):
        if os.path.isdir(p):
            shutil.rmtree(p, ignore_errors=True)
        elif os.path.isfile(p):
            try:
                os.remove(p)
            except OSError:
                pass
    src_copy = os.path.join(tmp, "in.asar")
    shutil.copy2(asar_path, src_copy)

    r = shutil.which("npx")
    if not r:
        return False, "npx not found (Node.js required for fallback)"
    import subprocess
    subprocess.run(["npx", "--yes", "@electron/asar", "extract", src_copy, work],
                   capture_output=True, text=True, timeout=600, shell=True)
    mj = os.path.join(work, "main.js")
    if not os.path.isfile(mj):
        return False, "extract failed"
    s = open(mj, encoding="utf-8", errors="replace").read()
    n = s.count("rejectUnauthorized: true")
    if n == 0:
        return False, "nothing to patch"
    open(mj, "w", encoding="utf-8").write(s.replace("rejectUnauthorized: true", "rejectUnauthorized:false"))
    subprocess.run(["npx", "--yes", "@electron/asar", "pack", work, out],
                   capture_output=True, text=True, timeout=600, shell=True)
    if not os.path.isfile(out):
        return False, "repack failed"
    shutil.copy2(out, asar_path)
    return True, "repackage OK (%d replaced)" % n

def asar_is_patched(asar_path):
    """已补丁 = main.js 区间没有 needle。"""
    try:
        with open(asar_path, "rb") as f:
            data = f.read()
        header, json_size, base = parse_asar_header(data)
        entry = header["files"]["main.js"]
        off = base + int(entry["offset"])
        size = int(entry["size"])
        return NEEDLE not in data[off:off + size]
    except Exception:
        return False

def read_only(asar_path, ro):
    try:
        import ctypes
        ctypes.windll.kernel32.SetFileAttributesW(asar_path, 0x1 if ro else 0x80)
    except Exception:
        pass

def main():
    src = find_client()
    if not src:
        print("[!] client not found (set XLB_CLIENT_DIR)")
        return 1
    asar = os.path.join(src, "resources", "app.asar")
    bak = asar + ".bak"
    print("[patch] client:", src)
    if not os.path.isfile(asar):
        print("[!] asar not found:", asar)
        return 1

    t0 = time.time()

    # 已补丁? (主文件区间无 needle)
    if asar_is_patched(asar):
        print("[=] already patched")
        return 0

    # 备份原版 (仅当不存在; 绝不用补丁版覆盖原版备份)
    if not os.path.isfile(bak):
        read_only(asar, False)
        shutil.copy2(asar, bak)
        read_only(asar, True)
        print("[+] backup saved: app.asar.bak")
    else:
        print("[=] backup exists")

    read_only(asar, False)
    try:
        hits, msg = patch_fast(asar)
        if hits > 0:
            print("[OK] fast patch: %d replaced in %.2fs (%s)" % (hits, time.time() - t0, msg))
            return 0
        print("[i] fast path: %s; trying repackage fallback..." % msg)
        ok, msg2 = patch_via_repackage(asar)
        if ok:
            print("[OK] %s in %.1fs" % (msg2, time.time() - t0))
            return 0
        print("[!] patch FAILED: %s" % msg2)
        return 1
    finally:
        read_only(asar, True)

if __name__ == "__main__":
    oplog.op("run", oplog.run_arg())
    sys.exit(main())
