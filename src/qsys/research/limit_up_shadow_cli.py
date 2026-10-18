"""Observe completed outcomes, then publish today's limit-up shadow ranking."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from qsys.ops.services import ServiceRun
from qsys.research.limit_up_shadow import latest_feature_session


def _run(command: list[str]) -> dict:
    try:
        completed = subprocess.run(command, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or "").strip()
        if len(detail) > 4_000:
            detail = detail[-4_000:]
        raise RuntimeError(
            f"subprocess failed ({exc.returncode}): {' '.join(command)}"
            + (f"\n{detail}" if detail else "")
        ) from exc
    lines = [line for line in completed.stdout.splitlines() if line.strip()]
    return json.loads(lines[-1]) if lines else {}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--warehouse", type=Path, required=True)
    parser.add_argument("--research-run", type=Path, required=True)
    parser.add_argument("--drift-reference", type=Path, required=True)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--config", required=True)
    parser.add_argument("--service-runs", type=Path, required=True)
    parser.add_argument("--threads", type=int, default=2)
    parser.add_argument("--min-eligible", type=int, default=3_000)
    args = parser.parse_args()

    python = sys.executable
    with ServiceRun.open(args.service_runs, "limit-up-shadow") as run:
        run.session = latest_feature_session(args.warehouse)
        with run.step("回填到期结果", interface="仓库只读", table="limit_up_shadow_outcomes") as step:
            result = _run([
                python, "-m", "qsys.research.limit_up_shadow_observe_cli",
                "--warehouse", str(args.warehouse), "--state", str(args.state),
            ])
            step.rows = len(result.get("recorded", []))
            step.detail = json.dumps(result, ensure_ascii=False)[:2_000]
        with run.step("生成影子榜单", interface="仓库与研究证据只读",
                      table="limit_up_shadow_snapshots") as step:
            result = _run([
                python, "-m", "qsys.research.limit_up_shadow_publish_cli",
                "--warehouse", str(args.warehouse),
                "--research-run", str(args.research_run),
                "--drift-reference", str(args.drift_reference),
                "--state", str(args.state), "--config", args.config,
                "--threads", str(max(1, args.threads)),
                "--min-eligible", str(max(1, args.min_eligible)),
            ])
            step.rows = int(result.get("entries", 0))
            step.detail = json.dumps(result, ensure_ascii=False)[:2_000]
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
