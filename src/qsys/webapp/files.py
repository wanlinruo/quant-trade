"""受控下载（#662 R3，总路线 v2 第 3 节）：取代旧页面的各类文件下载路由。

- 只读调用方给定的白名单根目录之下的文件；路径逐级校验，任何一级是符号链接都拒绝；
- 扩展名白名单（研究产物有清单时可不限扩展名）；有清单时校验字节数与 SHA-256；
- 任何不满足一律 404“文件不存在”，不区分原因，也不回显路径。
"""

from __future__ import annotations

import hashlib
import mimetypes
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

from fastapi import HTTPException
from fastapi.responses import Response

RUN_FILE_SUFFIXES = frozenset({".csv", ".json", ".txt", ".zip", ".py", ".log"})
_MEDIA = {
    ".csv": "text/csv; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".txt": "text/plain; charset=utf-8",
    ".log": "text/plain; charset=utf-8",
    ".py": "text/x-python; charset=utf-8",
    ".zip": "application/zip",
}


@dataclass(frozen=True)
class Expected:
    bytes: int
    sha256: str


def _missing() -> HTTPException:
    return HTTPException(status_code=404, detail="文件不存在")


def _parts(relative: str) -> list[str]:
    if not relative or "\\" in relative or relative.startswith("/"):
        raise _missing()
    parts = relative.split("/")
    if any(not p or p in (".", "..") or p.startswith(".") for p in parts):
        raise _missing()
    return parts


def _disposition(name: str) -> str:
    fallback = "".join(c if c.isascii() and c.isprintable() and c not in '"\\' else "_" for c in name)
    return f"attachment; filename=\"{fallback}\"; filename*=UTF-8''{quote(name, safe='')}"


def controlled_file(root: Path, relative: str, *, suffixes: frozenset[str] | None,
                    expected: Expected | None = None) -> Response:
    parts = _parts(relative)
    root = Path(root)
    if root.is_symlink() or not root.is_dir():
        raise _missing()
    current = root
    for part in parts:
        current = current / part
        if current.is_symlink():
            raise _missing()
    try:
        resolved = current.resolve(strict=True)
        base = root.resolve(strict=True)
    except (FileNotFoundError, OSError):
        raise _missing() from None
    if not resolved.is_relative_to(base) or not resolved.is_file():
        raise _missing()
    suffix = resolved.suffix.lower()
    if suffixes is not None and suffix not in suffixes:
        raise _missing()
    body = resolved.read_bytes()
    if expected is not None and (len(body) != expected.bytes
                                 or hashlib.sha256(body).hexdigest() != expected.sha256):
        raise _missing()
    media = _MEDIA.get(suffix) or mimetypes.guess_type(resolved.name)[0] or "application/octet-stream"
    return Response(content=body, media_type=media, headers={
        "Content-Disposition": _disposition(parts[-1]), "Cache-Control": "no-store"})
