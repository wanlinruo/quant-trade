#!/usr/bin/env python3
"""百度网盘取数工具（分页安全版）。

存在理由：baidupan-cli 的 `list` 不分页，百度 list 接口单次上限 1000 条，
目录一旦超过 1000 个条目就会被静默截断——没有任何报错。本脚本直调
REST 接口并翻页，保证清单完整。

凭证复用 baidupan-cli 的配置：~/.baidupan-cli/config.json
（本脚本只读该文件，不写、不打印 token。）

用法:
    python scripts/baidupan.py ls   <远程目录> [--recursive]
    python scripts/baidupan.py inv  <远程目录> --out <清单.json>
    python scripts/baidupan.py get  <远程目录> <本地目录> [--pattern '*.csv'] [--jobs 4]
"""
from __future__ import annotations

import argparse
import fnmatch
import json
import os
import pathlib
import sys
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

API = "https://pan.baidu.com/rest/2.0/xpan"
# 百度 dlink 强制校验 UA，非 pan.baidu.com 会返回 403。
UA = "pan.baidu.com"
PAGE = 1000
CONFIG = pathlib.Path.home() / ".baidupan-cli" / "config.json"


def token() -> str:
    if not CONFIG.exists():
        sys.exit(f"未找到凭证 {CONFIG}，请先运行: baidupan-cli auth -k <app-key> -s <secret-key>")
    cfg = json.loads(CONFIG.read_text())
    exp = cfg.get("expires_at", 0) / 1000
    if exp and exp < time.time():
        sys.exit("access_token 已过期，请重新运行 baidupan-cli auth")
    return cfg["access_token"]


def _get(url: str, retries: int = 3):
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.load(r)
        except Exception:
            if i == retries - 1:
                raise
            time.sleep(2 ** i)


def listdir(tok: str, path: str) -> list[dict]:
    """列目录，自动翻页直到取完。"""
    out, start = [], 0
    while True:
        q = urllib.parse.urlencode(
            {"method": "list", "access_token": tok, "dir": path,
             "start": start, "limit": PAGE, "order": "name"})
        data = _get(f"{API}/file?{q}")
        if data.get("errno"):
            raise RuntimeError(f"list {path} 失败 errno={data['errno']}")
        batch = data.get("list", [])
        out.extend(batch)
        if len(batch) < PAGE:
            return out
        start += PAGE


def walk(tok: str, path: str, recursive: bool = True):
    """深度优先遍历，yield 每个条目。"""
    for entry in listdir(tok, path):
        yield entry
        if entry["isdir"] and recursive:
            yield from walk(tok, entry["path"], True)


def dlink(tok: str, fs_id: int) -> str:
    q = urllib.parse.urlencode(
        {"method": "filemetas", "access_token": tok,
         "fsids": json.dumps([fs_id]), "dlink": "1"})
    data = _get(f"{API}/multimedia?{q}")
    return data["list"][0]["dlink"]


def download(tok: str, entry: dict, dest: pathlib.Path, retries: int = 4) -> tuple[str, bool]:
    """下载单个文件；已存在且大小一致则跳过（断点续传按文件粒度，不做分片）。

    服务端在并发偏高时会直接断开连接（RemoteDisconnected），故必须重试。
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and dest.stat().st_size == entry["size"]:
        return entry["path"], False
    tmp = dest.with_suffix(dest.suffix + ".part")
    for attempt in range(retries):
        try:
            # dlink 有时效，每次重试都重新申请。
            url = f"{dlink(tok, entry['fs_id'])}&access_token={tok}"
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=300) as r, open(tmp, "wb") as f:
                while chunk := r.read(1 << 20):
                    f.write(chunk)
            if tmp.stat().st_size != entry["size"]:
                raise RuntimeError(f"大小不符 {tmp.stat().st_size} != {entry['size']}")
            tmp.rename(dest)
            return entry["path"], True
        except Exception:
            tmp.unlink(missing_ok=True)
            if attempt == retries - 1:
                raise
            time.sleep(2 ** attempt)


def human(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.1f}{unit}"
        n /= 1024


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("ls", help="列目录（分页完整）")
    p.add_argument("path")
    p.add_argument("--recursive", "-r", action="store_true")

    p = sub.add_parser("inv", help="递归清单落成 JSON")
    p.add_argument("path")
    p.add_argument("--out", required=True)

    p = sub.add_parser("get", help="批量下载")
    p.add_argument("path")
    p.add_argument("dest")
    p.add_argument("--pattern", default="*")
    p.add_argument("--jobs", type=int, default=4)

    a = ap.parse_args()
    tok = token()

    if a.cmd == "ls":
        entries = list(walk(tok, a.path, a.recursive)) if a.recursive else listdir(tok, a.path)
        for e in entries:
            kind = "DIR " if e["isdir"] else human(e["size"]).rjust(8)
            print(f"{kind}  {e['path']}")
        files = [e for e in entries if not e["isdir"]]
        print(f"\n合计 {len(files)} 文件 / {human(sum(f['size'] for f in files))}"
              f"（目录 {len(entries) - len(files)}）")

    elif a.cmd == "inv":
        entries = list(walk(tok, a.path, True))
        pathlib.Path(a.out).write_text(
            json.dumps(entries, ensure_ascii=False, indent=1))
        files = [e for e in entries if not e["isdir"]]
        print(f"清单已写入 {a.out}: {len(files)} 文件 / {human(sum(f['size'] for f in files))}")

    elif a.cmd == "get":
        root = a.path.rstrip("/")
        files = [e for e in walk(tok, root, True)
                 if not e["isdir"] and fnmatch.fnmatch(e["server_filename"], a.pattern)]
        total = sum(f["size"] for f in files)
        print(f"待下载 {len(files)} 文件 / {human(total)} -> {a.dest}")
        dest_root = pathlib.Path(a.dest)
        done = [0, 0]  # [完成数, 已传字节]

        def job(e):
            rel = e["path"][len(root):].lstrip("/")
            _, fetched = download(tok, e, dest_root / rel)
            done[0] += 1
            done[1] += e["size"]
            tag = "↓" if fetched else "="
            print(f"[{done[0]}/{len(files)}] {tag} {human(done[1])}/{human(total)}  {rel}",
                  flush=True)

        with ThreadPoolExecutor(max_workers=a.jobs) as pool:
            list(pool.map(job, files))
        print(f"完成: {done[0]}/{len(files)}")


if __name__ == "__main__":
    main()
