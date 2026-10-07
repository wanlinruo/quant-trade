from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import zipfile
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from scripts.check_agent_alive import ALIVE, STALE, decide


def _load(path: str, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _builder():
    return _load("scripts/build_windows_release.py", "qsys_windows_release_builder")


def _entrypoint():
    return _load("deploy/windows/qsys-windows-deploy.py", "qsys_windows_deploy")


def _coordinator():
    return _load(
        "deploy/linux/qsys-windows-deploy-coordinator.py",
        "qsys_windows_deploy_coordinator",
    )


def _git_repo(tmp_path: Path) -> tuple[Path, str]:
    repo = tmp_path / "repo"
    (repo / "src" / "qsys").mkdir(parents=True)
    (repo / "scripts").mkdir()
    (repo / "src" / "qsys" / "__init__.py").write_text("VALUE = 1\n", encoding="utf-8")
    (repo / "scripts" / "run_agent.py").write_text("from qsys import VALUE\n", encoding="utf-8")
    (repo / "scripts" / "run_market_gateway.py").write_text(
        "from qsys import VALUE\n", encoding="utf-8",
    )
    subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(
        ["git", "-c", "user.name=test", "-c", "user.email=test@example.invalid",
         "commit", "-m", "fixture"],
        cwd=repo, check=True, capture_output=True,
    )
    sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
    return repo, sha


def test_release_builder_uses_exact_git_content_and_sha256(tmp_path: Path) -> None:
    builder = _builder()
    repo, sha = _git_repo(tmp_path)
    output = tmp_path / "live.zip"

    release = builder.build(repo, sha, "live_app", output)

    assert release["source_sha"] == sha
    assert sorted(release["files"]) == [
        "scripts/run_agent.py", "scripts/run_market_gateway.py", "src/qsys/__init__.py",
    ]
    with zipfile.ZipFile(output) as archive:
        assert json.loads(archive.read("release.json"))["files"] == release["files"]
        assert archive.read("payload/src/qsys/__init__.py") == b"VALUE = 1\n"


def test_windows_file_verification_normalizes_crlf_and_rejects_drift(tmp_path: Path) -> None:
    entrypoint = _entrypoint()
    root = tmp_path / "app"
    (root / "src").mkdir(parents=True)
    (root / "scripts").mkdir()
    (root / "src" / "module.py").write_bytes(b"one\r\ntwo\r\n")
    (root / "scripts" / "run.py").write_bytes(b"run\n")
    release = {
        "files": {
            "src/module.py": entrypoint.normalized_sha256(b"one\ntwo\n"),
            "scripts/run.py": entrypoint.normalized_sha256(b"run\n"),
        },
    }

    entrypoint.verify_files(root, release)
    (root / "src" / "module.py").write_text("changed\n", encoding="utf-8")
    with pytest.raises(ValueError, match="file drift"):
        entrypoint.verify_files(root, release)


def test_windows_archive_rejects_parent_traversal(tmp_path: Path) -> None:
    entrypoint = _entrypoint()
    archive = tmp_path / "bad.zip"
    with zipfile.ZipFile(archive, "w") as output:
        output.writestr("../escape.txt", "bad")

    with pytest.raises(ValueError, match="unsafe archive"):
        entrypoint.safe_extract(archive, tmp_path / "out")
    assert not (tmp_path / "escape.txt").exists()


def test_windows_config_fingerprint_redacts_secret_values(tmp_path: Path) -> None:
    entrypoint = _entrypoint()
    first = tmp_path / "first.toml"
    second = tmp_path / "second.toml"
    first.write_text('[redis]\nhost="127.0.0.1"\npassword="alpha"\n', encoding="utf-8")
    second.write_text('[redis]\nhost="127.0.0.1"\npassword="beta"\n', encoding="utf-8")

    assert entrypoint.config_public_fingerprint(first) == entrypoint.config_public_fingerprint(second)


def test_identical_sha_deployment_is_noop_and_does_not_touch_tasks(tmp_path: Path) -> None:
    entrypoint = _entrypoint()
    app = entrypoint.App("live_app", tmp_path / "live", "agent-task", "scripts/run_agent.py", "agent")
    config = entrypoint.Config(
        tmp_path / "python", tmp_path / "stage", tmp_path / "releases", "watchdog", {"live_app": app},
    )
    operation = {
        "schema_version": 1, "run_id": "run-1", "status": "prepared",
        "apps": {"live_app": {"state": "noop"}},
    }
    path = config.staging_root / "run-1" / "operation.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(operation), encoding="utf-8")

    class Host:
        def task(self, *args, **kwargs):
            raise AssertionError("noop must not touch scheduled tasks")

    result = entrypoint.deploy(config, "run-1", ["live_app"], Host())

    assert result == {"ok": True, "status": "success", "apps": ["live_app"], "noop": True}


def test_switch_failure_rolls_back_and_always_reenables_watchdog(
    tmp_path: Path, monkeypatch,
) -> None:
    entrypoint = _entrypoint()
    app = entrypoint.App("live_app", tmp_path / "live", "agent-task", "scripts/run_agent.py", "agent")
    config = entrypoint.Config(
        tmp_path / "python", tmp_path / "stage", tmp_path / "releases", "watchdog", {"live_app": app},
    )
    item = {"state": "prepared", "before_sha": "a" * 40}
    operation = {"schema_version": 1, "run_id": "run-2", "status": "prepared",
                 "apps": {"live_app": item}}
    path = config.staging_root / "run-2" / "operation.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(operation), encoding="utf-8")
    events: list[tuple] = []

    class Host:
        def task(self, *args, **kwargs):
            events.append(("task", *args))
            return subprocess.CompletedProcess(args, 0, "", "")

        def task_status(self, name):
            return {"state": "Ready", "last_run": "now", "last_result": 0}

    monkeypatch.setattr(entrypoint, "_wait_stopped", lambda *args: events.append(("stopped",)))
    monkeypatch.setattr(
        entrypoint, "_swap_in",
        lambda *args: (item.update({"state": "switched"}), events.append(("switched",))),
    )
    verifies = iter((RuntimeError("health"), None))

    def verify(*args):
        outcome = next(verifies)
        if outcome is not None:
            raise outcome

    monkeypatch.setattr(entrypoint, "_verify", verify)
    monkeypatch.setattr(entrypoint, "_restore", lambda *args: events.append(("restored",)))

    with pytest.raises(RuntimeError, match="health"):
        entrypoint.deploy(config, "run-2", ["live_app"], Host())

    assert ("restored",) in events
    assert events[-1] == ("task", "change", "watchdog", "/enable")
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["status"] == "rolled_back"
    assert saved["watchdog_restored"] is True


class _HeartbeatMonitor:
    def __init__(self, value: datetime):
        self.value = value

    def last_beat(self, source: str) -> datetime:
        return self.value


def test_release_health_requires_a_heartbeat_after_switch() -> None:
    now = datetime.now()
    heartbeat = now - timedelta(seconds=2)

    stale, _ = decide(
        _HeartbeatMonitor(heartbeat), "agent", 120, now,
        not_before_epoch=(now - timedelta(seconds=1)).timestamp(),
    )
    alive, _ = decide(
        _HeartbeatMonitor(heartbeat), "agent", 120, now,
        not_before_epoch=(now - timedelta(seconds=3)).timestamp(),
    )

    assert stale == STALE
    assert alive == ALIVE


def test_linux_coordinator_prepares_both_apps_without_switching_on_dry_run(
    tmp_path: Path, monkeypatch,
) -> None:
    coordinator = _coordinator()
    sha = "b" * 40
    calls: list[tuple] = []

    class Builder:
        @staticmethod
        def manifest(repo, current, app):
            return {"source_sha": current, "app": app, "files": {"src/old.py": "0" * 64}}

        @staticmethod
        def build(repo, target, app, output):
            output.write_bytes(b"zip")
            return {"source_tree": "c" * 40, "files": {"src/new.py": "1" * 64}}

    class Transport:
        def command(self, *args, check=True):
            calls.append(("command", *args))
            if args == ("status",):
                body = {"ok": True, "apps": {
                    "live_app": {"deployed_sha": "a" * 40},
                    "paper_app": {"deployed_sha": "a" * 40},
                }}
            elif args[0] == "prepare":
                body = {"ok": True, "status": "prepared"}
            else:
                raise AssertionError("dry-run must not call deploy")
            return subprocess.CompletedProcess(args, 0, json.dumps(body), "")

        def copy(self, local, remote_name):
            calls.append(("copy", remote_name))
            return "C:/incoming/" + remote_name

    monkeypatch.setattr(coordinator, "_builder_module", lambda repo, sha: Builder)
    result = coordinator.coordinate(
        tmp_path, sha, "run-3", ["live_app", "paper_app"],
        dry_run=True, transport=Transport(),
    )

    assert result["dry_run"] is True
    assert set(result["apps"]) == {"live_app", "paper_app"}
    assert not any(call[0] == "command" and call[1] == "deploy" for call in calls)
    assert sum(call[0] == "copy" for call in calls) == 4
