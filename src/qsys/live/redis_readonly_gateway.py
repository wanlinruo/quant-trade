"""Redis5 GET-only 控制面。独立固定副本运行，仅依赖 Python 标准库。

不导入业务包，不提供调用方指定的 Redis 命令、地址、DB 或模式匹配。
root 配置 UID→key；Linux SO_PEERCRED 提供身份，nft 门禁另行阻断直连。
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import json
import os
import pwd
from pathlib import Path
import re
import socket
import struct
import subprocess
import threading

MAX_VALUE = 4 * 1024 * 1024
MAX_REQUEST = 4096
TABLE = "qsys_redis_readonly"


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate field")
        result[key] = value
    return result


def parse(raw: bytes):
    return json.loads(raw, object_pairs_hook=unique_object)


def load_policy(path: Path) -> dict:
    info = path.stat()
    if info.st_uid != 0 or info.st_mode & 0o022:
        raise PermissionError("policy must be root-owned and not writable by group/other")
    policy = parse(path.read_bytes())
    if set(policy) != {"protocol", "redis", "readers", "blocked_uids"} or type(policy["protocol"]) is not int or policy["protocol"] != 1:
        raise ValueError("invalid policy")
    # 当前受核验的生产端点。配置改变需要另行评审，不能隐式连到其他库。
    if policy["redis"] != {"host": "127.0.0.1", "port": 6379, "db": 0}:
        raise ValueError("unsupported Redis endpoint")
    if not isinstance(policy["readers"], dict) or not policy["readers"]:
        raise ValueError("empty readers")
    for uid, access in policy["readers"].items():
        if not re.fullmatch(r"[1-9][0-9]*", uid) or not 1 <= int(uid) <= 2**32 - 2:
            raise ValueError("invalid UID")
        if not isinstance(access, dict) or set(access) != {"keys", "date_prefixes"}:
            raise ValueError("invalid access policy")
        for values in access.values():
            if not isinstance(values, list) or any(not isinstance(k, str) or not k or len(k.encode()) > 1024 or any(c in k for c in '\r\n\x00*?[]') for k in values):
                raise ValueError("invalid key policy")
    blocked = policy["blocked_uids"]
    if not isinstance(blocked, list) or any(type(uid) is not int or not 1 <= uid <= 2**32 - 2 for uid in blocked):
        raise ValueError("invalid blocked UID")
    return policy


def request_key(raw: bytes, uid: int, policy: dict) -> str:
    if len(raw) > MAX_REQUEST or not raw.endswith(b"\n"):
        raise ValueError("invalid framing")
    request = parse(raw)
    if (not isinstance(request, dict) or set(request) != {"protocol", "key"}
            or type(request["protocol"]) is not int or request["protocol"] != 1
            or not isinstance(request["key"], str)):
        raise ValueError("invalid request")
    access = policy["readers"].get(str(uid))
    if access is None:
        raise PermissionError("unknown peer")
    key = request["key"]
    if key in access["keys"]:
        return key
    for prefix in access["date_prefixes"]:
        if key.startswith(prefix + ":"):
            stamp = key[len(prefix) + 1:]
            if re.fullmatch(r"[0-9]{8}", stamp):
                day = datetime.strptime(stamp, "%Y%m%d")
                if day.strftime("%Y%m%d") == stamp:
                    return key
    raise PermissionError("key denied")


def redis_get(key: str) -> str | None:
    encoded = key.encode("utf-8")
    command = b"*2\r\n$3\r\nGET\r\n$" + str(len(encoded)).encode() + b"\r\n" + encoded + b"\r\n"
    with socket.create_connection(("127.0.0.1", 6379), timeout=2) as conn:
        conn.settimeout(2)
        conn.sendall(command)
        with conn.makefile("rb") as reader:
            header = reader.readline(64)
            if header == b"$-1\r\n":
                return None
            if not re.fullmatch(rb"\$(0|[1-9][0-9]*)\r\n", header):
                raise RuntimeError("invalid backend response")
            size = int(header[1:-2])
            if size > MAX_VALUE:
                raise RuntimeError("backend response too large")
            value = reader.read(size + 2)
            if len(value) != size + 2 or not value.endswith(b"\r\n"):
                raise RuntimeError("truncated backend response")
            return value[:-2].decode("utf-8")


def handle(conn: socket.socket, policy: dict) -> None:
    with conn:
        conn.settimeout(3)
        try:
            _, uid, _ = struct.unpack("3i", conn.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
            with conn.makefile("rb") as reader:
                key = request_key(reader.readline(MAX_REQUEST + 1), uid, policy)
            value = redis_get(key)
            reply = {"protocol": 1, "value": value}
        except Exception:
            # 不泄露键值、后端报错、凭据或用户配置，不把异常伪装为缺失值。
            reply = {"protocol": 1, "error": "request failed"}
        try:
            conn.sendall(json.dumps(reply, ensure_ascii=False).encode() + b"\n")
        except OSError:
            pass


def serve(policy: dict) -> None:
    if os.environ.get("LISTEN_PID") != str(os.getpid()) or os.environ.get("LISTEN_FDS") != "1":
        raise RuntimeError("exactly one systemd listening socket required")
    with socket.socket(fileno=3) as listener:
        if listener.family != socket.AF_UNIX or listener.type != socket.SOCK_STREAM or not listener.getsockopt(socket.SOL_SOCKET, socket.SO_ACCEPTCONN):
            raise RuntimeError("invalid activation socket")
        # 有界 worker 和排队：恶意/卡顿客户端不会积累无限任务。
        slots = threading.BoundedSemaphore(4)
        with ThreadPoolExecutor(max_workers=4) as workers:
            while True:
                conn, _ = listener.accept()
                if not slots.acquire(blocking=False):
                    conn.close()
                    continue
                future = workers.submit(handle, conn, policy)
                future.add_done_callback(lambda _: slots.release())


def nft_script(policy: dict, exists: bool) -> str:
    uids = sorted(set(policy["blocked_uids"]))
    if not uids:
        raise ValueError("empty network guard is not an enabled guard")
    reset = f"delete table inet {TABLE}\n" if exists else ""
    return reset + f"""table inet {TABLE} {{
 chain output {{
  type filter hook output priority 0; policy accept;
  meta skuid {{ {', '.join(map(str, uids))} }} tcp dport 6379 counter reject with tcp reset
 }}
}}
"""


READER_USERS = {"qsys-web", "qsys-health-observer", "qsys-daily-digest", "qsys-reboot-drill"}
GUARDED_USERS = READER_USERS | {"qsys-service-checks"}


def check_identities(policy: dict) -> None:
    for uid in policy["readers"]:
        if pwd.getpwuid(int(uid)).pw_name not in READER_USERS:
            raise ValueError("reader UID is outside reviewed components")
    for uid in policy["blocked_uids"]:
        if pwd.getpwuid(uid).pw_name not in GUARDED_USERS:
            raise ValueError("network guard UID is outside reviewed components")


UNIT_FILES = {
    "qsys-redis-reader.socket": """[Unit]
Description=Qsys Redis5 read-only peer authenticated socket
[Socket]
ListenStream=/run/qsys-redis-reader.sock
SocketUser=root
SocketGroup=qsys-observers
SocketMode=0660
Backlog=32
RemoveOnStop=yes
[Install]
WantedBy=sockets.target
""",
    "qsys-redis-reader.service": """[Unit]
Description=Qsys Redis5 GET-only capability gateway
Requires=qsys-redis-reader.socket
After=qsys-redis-reader.socket network.target
[Service]
User=qsys-redis-reader
Group=qsys-redis-reader
ExecStart=/usr/bin/python3 -I /usr/local/libexec/qsys-redis-reader.py serve --policy /etc/qsys/redis-reader/policy.json
Restart=on-failure
RestartSec=3
NoNewPrivileges=yes
CapabilityBoundingSet=
ProtectSystem=strict
ProtectHome=yes
PrivateTmp=yes
PrivateDevices=yes
RestrictSUIDSGID=yes
RestrictNamespaces=yes
RestrictAddressFamilies=AF_UNIX AF_INET
IPAddressDeny=any
IPAddressAllow=127.0.0.1/32
MemoryMax=256M
CPUQuota=25%
TasksMax=12
UMask=0077
""",
    "qsys-redis-readonly-netguard.service": """[Unit]
Description=Qsys read-only component direct Redis port guard
After=network-pre.target
Before=qsys-monitor.service qsys-health-observer.service qsys-daily-digest.service qsys-reboot-drill.service qsys-service-checks.service
[Service]
Type=oneshot
ExecStart=/usr/bin/python3 -I /usr/local/libexec/qsys-redis-reader.py netguard --policy /etc/qsys/redis-reader/policy.json
ExecReload=/usr/bin/python3 -I /usr/local/libexec/qsys-redis-reader.py netguard --policy /etc/qsys/redis-reader/policy.json
RemainAfterExit=yes
NoNewPrivileges=yes
CapabilityBoundingSet=CAP_NET_ADMIN
ProtectSystem=strict
ProtectHome=yes
PrivateTmp=yes
RestrictAddressFamilies=AF_NETLINK AF_UNIX
[Install]
WantedBy=multi-user.target
""",
}


def install_control_plane() -> None:
    if os.getuid() != 0:
        raise PermissionError("root required")
    target = Path("/usr/local/libexec/qsys-redis-reader.py")
    # 仅从合并后的精确GitHub SHA运行此操作；不依赖共享checkout/venv。
    target.parent.mkdir(parents=True, exist_ok=True)
    files = {target: Path(__file__).read_bytes()}
    files.update({Path("/etc/systemd/system") / name: text.encode() for name, text in UNIT_FILES.items()})
    for path, raw in files.items():
        if path.is_symlink():
            raise PermissionError("refuse symlink target")
        temporary = path.with_name(path.name + ".new")
        # x 模式拒绝预先放置的临时文件/软链接，失败不覆盖未知对象。
        with temporary.open("xb") as output:
            output.write(raw)
        os.chmod(temporary, 0o644)
        os.chown(temporary, 0, 0)
        temporary.replace(path)


def netguard(policy: dict) -> None:
    if os.getuid() != 0:
        raise PermissionError("root required")
    # 查询失败不得误认作表不存在；先查询整个表清单，保持其他表不变。
    tables = subprocess.run(["/usr/sbin/nft", "-j", "list", "tables"], capture_output=True, check=True)
    existing = parse(tables.stdout)["nftables"]
    exists = any(t.get("table", {}).get("family") == "inet" and t.get("table", {}).get("name") == TABLE for t in existing)
    raw = nft_script(policy, exists).encode()
    subprocess.run(["/usr/sbin/nft", "--check", "-f", "-"], input=raw, check=True)
    # nft -f 事务原子替换本表；不 flush ruleset，不 touch 交易/隧道 UID。
    subprocess.run(["/usr/sbin/nft", "-f", "-"], input=raw, check=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=["serve", "netguard", "check", "install"])
    parser.add_argument("--policy", type=Path, required=True)
    args = parser.parse_args()
    policy = load_policy(args.policy)
    check_identities(policy)
    if args.operation == "serve":
        serve(policy)
    elif args.operation == "netguard":
        netguard(policy)
    elif args.operation == "install":
        install_control_plane()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
