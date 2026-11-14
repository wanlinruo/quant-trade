#!/usr/bin/env python3
"""组件旧 release 清理：只保留 current 与最近一次成功生产 record 的回退目标（#126）。

维护者 2026-11-14 裁决保留策略。两步走，删除前必须由维护者确认精确清单：

    sudo python3 prune_component_releases.py plan  --out PLAN.json
    sudo python3 prune_component_releases.py apply --plan PLAN.json \\
        --plan-sha256 <plan 输出的哈希> --run-dir /data/qsys/var-deployments/<新目录>

只依赖标准库，可在节点上直接运行。路径全部取自节点可信白名单
``/etc/qsys/component-releases.json``（与安装器一致），不硬编码组件布局。

安全约束：
- 没有成功生产 record 的组件整组跳过，不猜回退目标；
- current（解析后的真实路径）与回退目标永不进入清单；
- 有进程（exe/cwd/fd/maps）、systemd unit 或 /etc/qsys 配置、软链接引用的候选，整份清单拒绝；
- apply 时重新生成清单，必须与已确认清单逐项相同；current 指针与服务进程身份前后必须一致。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

WHITELIST = Path("/etc/qsys/component-releases.json")
RECORDS = Path("/data/qsys/var-deployments/github")
CONFIG_ROOTS = (Path("/etc/systemd/system"), Path("/etc/qsys"))
LINK_ROOTS = (Path("/opt/qsys"), Path("/opt/qsys-data"), Path("/etc/qsys"),
              Path("/home/ubuntu/qsys"), Path("/usr/local/bin"))
SHA = re.compile(r"[0-9a-f]{40}")


@dataclass(frozen=True)
class Spec:
    component: str
    release_root: Path
    current: Path


def load_specs(path: Path = WHITELIST) -> list[Spec]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or raw.get("schema_version") != 1 \
            or not isinstance(raw.get("components"), dict):
        raise ValueError("组件白名单结构不符（需 schema_version=1 与 components）")
    items = raw["components"]
    specs = []
    for name, spec in sorted(items.items()):
        if not isinstance(spec, dict) or "release_root" not in spec or "current" not in spec:
            continue
        specs.append(Spec(name, Path(spec["release_root"]), Path(spec["current"])))
    return specs


def latest_rollbacks(records: Path = RECORDS) -> dict[str, str]:
    """每个组件最近一次 success 的生产 record 中登记的回退目标。"""
    best: dict[str, tuple[str, str]] = {}
    for path in records.glob("*/record.json"):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if record.get("status") != "success" or record.get("environment") != "production":
            continue
        finished = str(record.get("completed_at") or "")
        for component in record.get("components") or []:
            target = (record.get("rollback_targets") or {}).get(component)
            if target is None:
                continue
            if component not in best or finished > best[component][0]:
                best[component] = (finished, str(target))
    return {component: target for component, (_, target) in best.items()}


def plan(specs: list[Spec], rollbacks: dict[str, str]) -> dict:
    candidates, kept, skipped = [], [], []
    for spec in specs:
        if not spec.release_root.is_dir():
            continue
        if spec.component not in rollbacks:
            skipped.append({"component": spec.component, "reason": "没有成功的生产 record"})
            continue
        current = spec.current.resolve() if spec.current.exists() else None
        rollback = rollbacks[spec.component]
        for release in sorted(spec.release_root.iterdir()):
            if not SHA.fullmatch(release.name) or release.is_symlink() or not release.is_dir():
                continue
            real = release.resolve()
            if real == current:
                kept.append({"component": spec.component, "release": release.name, "role": "current"})
            elif release.name == rollback:
                kept.append({"component": spec.component, "release": release.name, "role": "rollback"})
            else:
                candidates.append({"component": spec.component, "release": release.name,
                                   "path": str(real)})
    return {"candidates": candidates, "kept": kept, "skipped": skipped}


def _inside(path: str, roots: list[str]) -> bool:
    return any(path == root or path.startswith(root + "/") for root in roots)


def references(paths: list[str], proc: Path = Path("/proc"),
               config_roots: tuple[Path, ...] = CONFIG_ROOTS,
               link_roots: tuple[Path, ...] = LINK_ROOTS) -> dict[str, list]:
    found: dict[str, list] = {"process": [], "config": [], "symlink": []}
    if not paths:
        return found
    for pid in (p for p in proc.iterdir() if p.name.isdigit()) if proc.is_dir() else ():
        seen: list[str] = []
        for link in ("exe", "cwd", "root"):
            try:
                seen.append(os.readlink(pid / link))
            except OSError:
                pass
        try:
            seen += [os.readlink(fd) for fd in (pid / "fd").iterdir()]
        except OSError:
            pass
        try:
            seen += [line.split()[-1] for line in (pid / "maps").read_text().splitlines()
                     if "/" in line]
        except OSError:
            pass
        if any(_inside(item, paths) for item in seen):
            found["process"].append(pid.name)
    names = {Path(p).name for p in paths}
    for root in config_roots:
        if not root.is_dir():
            continue
        for file in root.rglob("*"):
            if file.is_file() and not file.is_symlink():
                try:
                    text = file.read_text(encoding="utf-8", errors="ignore")
                except OSError:
                    continue
                if any(name in text for name in names):
                    found["config"].append(str(file))
    for root in link_roots:
        if not root.is_dir():
            continue
        for dirpath, dirnames, filenames in os.walk(root):
            if _inside(dirpath, paths):
                dirnames[:] = []
                continue
            for name in dirnames + filenames:
                full = os.path.join(dirpath, name)
                if os.path.islink(full) and _inside(os.path.realpath(full), paths):
                    found["symlink"].append(full)
    return found


def inode_estimate(paths: list[str]) -> dict[str, int]:
    inodes: dict[tuple[int, int], list[int]] = {}
    for root in paths:
        for dirpath, _, filenames in os.walk(root):
            for name in filenames:
                try:
                    st = os.lstat(os.path.join(dirpath, name))
                except OSError:
                    continue
                entry = inodes.setdefault((st.st_dev, st.st_ino), [st.st_blocks * 512, st.st_nlink, 0])
                entry[2] += 1
    union = sum(v[0] for v in inodes.values())
    external = sum(v[0] for v in inodes.values() if v[1] > v[2])
    return {"inodes": len(inodes), "union_bytes": union, "external_linked_bytes": external,
            "estimated_free_bytes": union - external}


def identity(specs: list[Spec]) -> list[str]:
    lines = [f"{s.component} {s.current.resolve() if s.current.exists() else '-'}" for s in specs]
    units = subprocess.run(
        ["systemctl", "list-units", "qsys-*", "--type=service", "--state=active",
         "--no-legend", "--plain"], capture_output=True, text=True, check=False,
    ).stdout.split()
    for unit in sorted(u for u in units if u.endswith(".service")):
        props = subprocess.run(
            ["systemctl", "show", unit, "-p", "MainPID", "-p",
             "ExecMainStartTimestampMonotonic", "-p", "NRestarts", "--value"],
            capture_output=True, text=True, check=False,
        ).stdout.split()
        lines.append(f"{unit} {' '.join(props)}")
    return lines


def _plan_bytes(content: dict) -> bytes:
    return json.dumps(content, ensure_ascii=False, sort_keys=True, indent=1).encode("utf-8")


def cmd_plan(args, specs=None, rollbacks=None) -> int:
    specs = specs if specs is not None else load_specs(args.whitelist)
    result = plan(specs, rollbacks if rollbacks is not None else latest_rollbacks(args.records))
    paths = [c["path"] for c in result["candidates"]]
    result["references"] = references(paths)
    result["estimate"] = inode_estimate(paths)
    result["generated_at"] = datetime.now().astimezone().isoformat()
    data = _plan_bytes(result)
    Path(args.out).write_bytes(data)
    for item in result["candidates"]:
        print(f"CANDIDATE {item['component']} {item['release'][:12]}")
    for item in result["skipped"]:
        print(f"SKIPPED {item['component']}: {item['reason']}")
    refs = sum(len(v) for v in result["references"].values())
    gib = result["estimate"]["estimated_free_bytes"] / 2**30
    print(f"candidates={len(paths)} references={refs} estimated_free={gib:.2f}GiB")
    print(f"plan_sha256={hashlib.sha256(data).hexdigest()}")
    return 1 if refs else 0


def cmd_apply(args, specs=None, rollbacks=None) -> int:
    data = Path(args.plan).read_bytes()
    if hashlib.sha256(data).hexdigest() != args.plan_sha256:
        print("计划文件哈希与确认值不符，拒绝执行", file=sys.stderr)
        return 2
    confirmed = json.loads(data)
    run = Path(args.run_dir)
    if run.exists():
        print(f"运行目录已存在：{run}", file=sys.stderr)
        return 2
    specs = specs if specs is not None else load_specs(args.whitelist)
    fresh = plan(specs, rollbacks if rollbacks is not None else latest_rollbacks(args.records))
    if fresh["candidates"] != confirmed["candidates"]:
        print("当前状态与已确认清单不一致（发布或指针已变化），拒绝执行", file=sys.stderr)
        return 2
    paths = [c["path"] for c in fresh["candidates"]]
    refs = references(paths)
    if any(refs.values()):
        print(f"候选仍被引用，拒绝执行：{refs}", file=sys.stderr)
        return 2
    run.mkdir(parents=True)
    (run / "plan.json").write_bytes(data)
    (run / "refs-check.json").write_text(json.dumps(refs))
    before = identity(specs)
    (run / "identity-before.txt").write_text("\n".join(before) + "\n")
    disk_before = {p: shutil.disk_usage(p).free for p in paths}
    with (run / "manifest-inodes.tsv").open("w", encoding="utf-8") as manifest:
        for root in paths:
            for dirpath, _, filenames in os.walk(root):
                for name in filenames:
                    full = os.path.join(dirpath, name)
                    st = os.lstat(full)
                    manifest.write(f"{st.st_ino}\t{st.st_nlink}\t{st.st_size}\t{full}\n")
    for root in paths:
        shutil.rmtree(root)
        print(f"deleted {root}")
    after = identity(specs)
    (run / "identity-after.txt").write_text("\n".join(after) + "\n")
    freed = {p: shutil.disk_usage(Path(p).parent).free - disk_before[p] for p in paths}
    (run / "result.json").write_text(json.dumps({"deleted": paths, "free_delta_by_path": freed,
                                                 "identity_unchanged": before == after}))
    if before != after:
        print("IDENTITY CHANGED：删除前后 current 指针或服务进程不同，请立即核查", file=sys.stderr)
        return 3
    print("identity unchanged")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--whitelist", type=Path, default=WHITELIST)
    parser.add_argument("--records", type=Path, default=RECORDS)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("plan", help="只读生成清单")
    p.add_argument("--out", required=True)
    a = sub.add_parser("apply", help="按已确认清单删除")
    a.add_argument("--plan", required=True)
    a.add_argument("--plan-sha256", required=True)
    a.add_argument("--run-dir", required=True)
    args = parser.parse_args(argv)
    return cmd_plan(args) if args.command == "plan" else cmd_apply(args)


if __name__ == "__main__":
    sys.exit(main())
