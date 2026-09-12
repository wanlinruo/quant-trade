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
import threading
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from collections.abc import Callable

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


def download(tok: str, entry: dict, dest: pathlib.Path, retries: int = 4,
             initial_link: str | None = None) -> tuple[str, bool]:
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
            link = initial_link if attempt == 0 and initial_link else dlink(tok, entry['fs_id'])
            url = f"{link}&access_token={tok}"
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


def download_resumable(
    tok: str,
    entry: dict,
    dest: pathlib.Path,
    *,
    retries: int = 50,
    progress: Callable[[int, int], None] | None = None,
) -> tuple[str, bool]:
    """断点续传单个大文件，失败时保留 ``.part`` 并刷新 dlink。

    百度返回 206 时才在已有临时文件后追加。如果服务端忽略 Range 返回 200，
    从零覆盖临时文件，绝不把完整响应误追加到半截文件后。
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    expected = int(entry["size"])
    if dest.exists() and dest.stat().st_size == expected:
        return entry["path"], False
    tmp = dest.with_suffix(dest.suffix + ".part")
    if tmp.exists() and tmp.stat().st_size > expected:
        tmp.unlink()

    for attempt in range(retries):
        offset = tmp.stat().st_size if tmp.exists() else 0
        try:
            link = dlink(tok, entry["fs_id"])
            url = f"{link}&access_token={tok}"
            headers = {"User-Agent": UA, "Accept-Encoding": "identity"}
            if offset:
                headers["Range"] = f"bytes={offset}-"
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=300) as response:
                status = getattr(response, "status", response.getcode())
                if offset and status == 206:
                    content_range = response.headers.get("Content-Range", "")
                    if not content_range.startswith(f"bytes {offset}-"):
                        raise RuntimeError("服务器返回了错误的续传起点")
                    mode = "ab"
                elif status == 200:
                    offset = 0
                    mode = "wb"
                else:
                    raise RuntimeError(f"不支持的 HTTP 状态 {status}")
                with open(tmp, mode) as output:
                    while chunk := response.read(4 << 20):
                        output.write(chunk)
                        offset += len(chunk)
                        if offset > expected:
                            raise RuntimeError("下载字节数超过远端清单")
                        if progress is not None:
                            progress(offset, expected)
                    output.flush()
                    os.fsync(output.fileno())
            if tmp.stat().st_size != expected:
                raise RuntimeError(f"大小不符 {tmp.stat().st_size} != {expected}")
            os.replace(tmp, dest)
            return entry["path"], True
        except Exception:
            if attempt == retries - 1:
                raise
            time.sleep(min(2 ** min(attempt, 5), 30))


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
    p.add_argument("--jobs", type=int, choices=range(1, 4), default=3)
    p.add_argument("--inventory", help="复用已核验的分页完整清单，避免再次遍历远端")

    p = sub.add_parser("get-file", help="断点续传一个大文件")
    p.add_argument("path", help="远端完整文件路径")
    p.add_argument("dest", help="本地目标文件")

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
        entries = json.loads(pathlib.Path(a.inventory).read_text()) if a.inventory else walk(tok, root, True)
        files = [e for e in entries
                 if not e["isdir"] and e["path"].startswith(root + "/")
                 and fnmatch.fnmatch(e["server_filename"], a.pattern)]
        files.sort(key=lambda e: e["path"])
        total = sum(f["size"] for f in files)
        print(f"待下载 {len(files)} 文件 / {human(total)} -> {a.dest}")
        dest_root = pathlib.Path(a.dest)
        done = [0, 0]  # [完成数, 已传字节]
        lock = threading.Lock()
        failures = []

        def job(e, link=None):
            rel = e["path"][len(root):].lstrip("/")
            try:
                _, fetched = download(tok, e, dest_root / rel, initial_link=link)
            except Exception as exc:
                with lock:
                    failures.append(rel)
                    # 不输出可能含有授权 URL 的异常内容。
                    print(f"FAILED {rel} {type(exc).__name__}", flush=True)
                return
            with lock:
                done[0] += 1
                done[1] += e["size"]
                tag = "↓" if fetched else "="
                print(f"[{done[0]}/{len(files)}] {tag} {human(done[1])}/{human(total)}  {rel}",
                      flush=True)

        with ThreadPoolExecutor(max_workers=a.jobs) as pool:
            for offset in range(0, len(files), 100):
                batch = files[offset:offset + 100]
                pending = [e for e in batch if not (dest_root / e["path"][len(root) + 1:]).exists()
                           or (dest_root / e["path"][len(root) + 1:]).stat().st_size != e["size"]]
                links = {}
                if pending:
                    q = urllib.parse.urlencode({"method": "filemetas", "access_token": tok,
                        "fsids": json.dumps([e["fs_id"] for e in pending]), "dlink": "1"})
                    try:
                        response = _get(f"{API}/multimedia?{q}")
                        links = {e["fs_id"]: e["dlink"] for e in response.get("list", []) if e.get("dlink")}
                    except Exception:
                        pass  # 单个文件的下载路径仍会获取链接并重试。
                futures = [pool.submit(job, e, links.get(e["fs_id"])) for e in batch]
                for future in futures:
                    future.result()
        print(f"完成: {done[0]}/{len(files)}")
        if failures:
            sys.exit(f"{len(failures)} 文件失败；重跑相同命令可续传")

    elif a.cmd == "get-file":
        remote = pathlib.PurePosixPath(a.path)
        entries = [e for e in listdir(tok, str(remote.parent))
                   if not e["isdir"] and e["path"] == a.path]
        if len(entries) != 1:
            sys.exit(f"远端文件不存在或不唯一: {a.path}")
        entry = entries[0]
        last_print = [0.0]

        def show(done: int, total: int) -> None:
            now = time.monotonic()
            if now - last_print[0] >= 5 or done == total:
                print(f"{human(done)}/{human(total)} ({done / total:.1%})", flush=True)
                last_print[0] = now

        print(f"断点续传 {entry['path']} ({human(entry['size'])}) -> {a.dest}")
        download_resumable(tok, entry, pathlib.Path(a.dest), progress=show)
        print("下载完成，字节数与远端清单一致")


if __name__ == "__main__":
    main()
