from __future__ import annotations

import hashlib
import json
import subprocess
import zipfile
from pathlib import Path

import pytest

from scripts.build_component_release import build, verify


def test_sqlite_seed_only_accepts_the_pinned_legacy_source(tmp_path):
    from scripts.component_release_config import load_component_releases_file

    path=tmp_path/"components.json"
    specs=json.loads(Path("deploy/component-releases.json").read_text())
    path.write_text(json.dumps(specs))
    assert load_component_releases_file(path)["limit-up-shadow"]["seed"]["source"] == \
        "/data/qsys/var-prod/limit-up-shadow.sqlite3"
    specs["components"]["limit-up-shadow"]["seed"]["source"]="/etc/other.sqlite3"
    path.write_text(json.dumps(specs))
    with pytest.raises(ValueError,match="fixed legacy state"):
        load_component_releases_file(path)
    specs=json.loads(Path("deploy/component-releases.json").read_text())
    specs["components"]["catalog-verify"]["seed"]["source"]="/etc/other.json"
    path.write_text(json.dumps(specs))
    with pytest.raises(ValueError,match="relative path"):
        load_component_releases_file(path)


def _repo(tmp_path: Path) -> tuple[Path, str]:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "pyproject.toml").write_text("[project]\nname='qsys'\n", encoding="utf-8")
    (repo / "uv.lock").write_text("version = 1\n", encoding="utf-8")
    (repo / "src/qsys/data").mkdir(parents=True)
    (repo / "src/qsys/strategy").mkdir(parents=True)
    (repo / "src/qsys/data/example.py").write_text("DATA = 1\n", encoding="utf-8")
    (repo / "src/qsys/strategy/example.py").write_text(
        "STRATEGY = 1\n", encoding="utf-8",
    )
    for package in ("backtest", "core", "live", "markets", "ops", "paper", "web"):
        (repo / f"src/qsys/{package}").mkdir(parents=True)
        (repo / f"src/qsys/{package}/example.py").write_text(
            f"PACKAGE = {package!r}\n", encoding="utf-8",
        )
    (repo / "src/qsys/web/workbench.py").write_text(
        "WORKBENCH = 1\n", encoding="utf-8",
    )
    (repo / "src/qsys/web/backtest_worker_cli.py").write_text(
        "WORKER_CLI = 1\n", encoding="utf-8",
    )
    (repo / "scripts").mkdir()
    (repo / "scripts/win_download_minutes.py").write_text(
        "WINDOWS_DOWNLOADER = 1\n", encoding="utf-8",
    )
    (repo / "scripts/win_download_auction_ticks.py").write_text("AUCTION = 1\n")
    (repo / "scripts/win_download_dividends.py").write_text("DIVIDENDS = 1\n")
    (repo / "scripts/unrelated.py").write_text(
        "UNRELATED = 1\n", encoding="utf-8",
    )
    (repo / "src/qsys/ops/sqlite_reader_anchor.py").write_text("READER = 1\n")
    (repo / "src/qsys/__init__.py").write_text("")
    (repo / "src/qsys/ops/__init__.py").write_text("")
    (repo / "deploy").mkdir()
    (repo / "deploy/component-releases.json").write_text(
        Path("deploy/component-releases.json").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    subprocess.run(["git", "init", "-b", "test"], cwd=repo, check=True,
                   capture_output=True)
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(
        ["git", "-c", "user.name=test", "-c", "user.email=test@example.invalid",
         "commit", "-m", "fixture"], cwd=repo, check=True, capture_output=True,
    )
    sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo,
                                  text=True).strip()
    return repo, sha


def _wheel(
    path: Path, *, command: str = "qsys-catalog-verify",
    entrypoint: str = "qsys.data.catalog_verify_cli:main",
) -> Path:
    path.parent.mkdir(parents=True)
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("qsys-0.1.0.dist-info/METADATA", "Name: qsys\nVersion: 0.1.0\n")
        archive.writestr(
            "qsys-0.1.0.dist-info/entry_points.txt",
            f"[console_scripts]\n{command} = {entrypoint}\n",
        )
        archive.writestr("qsys/__init__.py", "")
    return path


def test_state_reader_release_binds_only_its_stdlib_source(tmp_path: Path) -> None:
    repo, sha = _repo(tmp_path)
    wheel = _wheel(
        tmp_path / "dist-reader" / "qsys-0.1.0-py3-none-any.whl",
        command="qsys-live-state-reader", entrypoint="qsys.ops.sqlite_reader_anchor:main",
    )
    release = tmp_path / "reader-release"
    value = build(repo, sha, "live-state-reader", wheel.parent, release,
                  repository="owner/repo", run_id="544", run_attempt="1")
    assert [record["path"] for record in value["scan_files"]] == [
        "src/qsys/__init__.py", "src/qsys/ops/__init__.py", "src/qsys/ops/sqlite_reader_anchor.py",
    ]
    assert verify(repo, sha, "live-state-reader", release) == value


def test_catalog_release_binds_entrypoint_wheel_and_tree(tmp_path: Path) -> None:
    repo, sha = _repo(tmp_path)
    wheel = _wheel(tmp_path / "dist" / "qsys-0.1.0-py3-none-any.whl")
    release = tmp_path / "release"

    value = build(
        repo, sha, "catalog-verify", wheel.parent, release,
        repository="owner/repo", run_id="123", run_attempt="1",
    )

    assert value["component"] == "catalog-verify"
    assert value["source_sha"] == sha
    assert value["version"] == 4
    assert len(value["component_spec_sha256"]) == 64
    assert value["wheel_filename"] == wheel.name
    assert value["wheel_sha256"] == hashlib.sha256(wheel.read_bytes()).hexdigest()
    assert [item["path"] for item in value["scan_files"]] == [
        "src/qsys/data/example.py", "src/qsys/strategy/example.py",
    ]
    assert (release / "source/src/qsys/data/example.py").read_text() == "DATA = 1\n"
    assert verify(repo, sha, "catalog-verify", release) == value
    assert json.loads((release / "release.json").read_text()) == value


def test_catalog_release_rejects_wrong_entrypoint_and_tampering(tmp_path: Path) -> None:
    repo, sha = _repo(tmp_path)
    wheel = _wheel(
        tmp_path / "bad" / "qsys-0.1.0-py3-none-any.whl",
        entrypoint="qsys.research.runner_cli:main",
    )
    with pytest.raises(ValueError, match="does not expose"):
        build(repo, sha, "catalog-verify", wheel.parent, tmp_path / "bad-release",
              repository="owner/repo", run_id="123", run_attempt="1")

    wheel = _wheel(tmp_path / "dist" / "qsys-0.1.0-py3-none-any.whl")
    release = tmp_path / "release"
    build(repo, sha, "catalog-verify", wheel.parent, release,
          repository="owner/repo", run_id="123", run_attempt="1")
    with (release / "dist" / wheel.name).open("ab") as stream:
        stream.write(b"tampered")
    with pytest.raises(ValueError, match="wheel_sha256 mismatch"):
        verify(repo, sha, "catalog-verify", release)

    build(repo, sha, "catalog-verify", wheel.parent, tmp_path / "source-release",
          repository="owner/repo", run_id="123", run_attempt="1")
    source = tmp_path / "source-release/source/src/qsys/data/example.py"
    source.write_text("DATA = 2\n", encoding="utf-8")
    with pytest.raises(ValueError, match="scan source mismatch"):
        verify(repo, sha, "catalog-verify", tmp_path / "source-release")

    spec_release = tmp_path / "spec-release"
    build(repo, sha, "catalog-verify", wheel.parent, spec_release,
          repository="owner/repo", run_id="123", run_attempt="1")
    manifest = json.loads((spec_release / "release.json").read_text(encoding="utf-8"))
    manifest["component_spec_sha256"] = "0" * 64
    (spec_release / "release.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="component specification mismatch"):
        verify(repo, sha, "catalog-verify", spec_release)


def test_service_checks_release_uses_its_own_entrypoint_and_scan_roots(tmp_path: Path) -> None:
    repo, sha = _repo(tmp_path)
    wheel = _wheel(
        tmp_path / "dist-service" / "qsys-0.1.0-py3-none-any.whl",
        command="qsys-service-checks", entrypoint="qsys.ops.service_checks_cli:main",
    )
    release = tmp_path / "service-release"

    value = build(
        repo, sha, "service-checks", wheel.parent, release,
        repository="owner/repo", run_id="456", run_attempt="1",
    )

    assert value["entrypoint"] == {
        "command": "qsys-service-checks", "target": "qsys.ops.service_checks_cli:main",
    }
    assert {record["path"].split("/")[2] for record in value["scan_files"]} == {
        "data", "live", "ops", "paper",
    }
    assert verify(repo, sha, "service-checks", release) == value


def test_backtest_worker_release_binds_worker_dependencies(tmp_path: Path) -> None:
    repo, sha = _repo(tmp_path)
    wheel = _wheel(
        tmp_path / "dist-worker" / "qsys-0.1.0-py3-none-any.whl",
        command="qsys-backtest-worker",
        entrypoint="qsys.web.backtest_worker_cli:main",
    )
    release = tmp_path / "worker-release"

    value = build(
        repo, sha, "backtest-worker", wheel.parent, release,
        repository="owner/repo", run_id="457", run_attempt="1",
    )

    assert value["entrypoint"] == {
        "command": "qsys-backtest-worker",
        "target": "qsys.web.backtest_worker_cli:main",
    }
    paths = {record["path"] for record in value["scan_files"]}
    assert {path.split("/")[2] for path in paths} == {
        "backtest", "core", "data", "markets", "strategy", "web",
    }
    assert "src/qsys/web/workbench.py" in paths
    assert "src/qsys/web/backtest_worker_cli.py" in paths
    assert "src/qsys/web/example.py" not in paths
    assert verify(repo, sha, "backtest-worker", release) == value


def test_web_release_binds_packaged_entrypoint_and_all_runtime_sources(
    tmp_path: Path,
) -> None:
    repo, sha = _repo(tmp_path)
    wheel = _wheel(
        tmp_path / "dist-web" / "qsys-0.1.0-py3-none-any.whl",
        command="qsys-web", entrypoint="qsys.web.monitor_cli:main",
    )
    release = tmp_path / "web-release"

    value = build(
        repo, sha, "web", wheel.parent, release,
        repository="owner/repo", run_id="458", run_attempt="1",
    )

    assert value["entrypoint"] == {
        "command": "qsys-web", "target": "qsys.web.monitor_cli:main",
    }
    paths = {record["path"] for record in value["scan_files"]}
    assert "src/qsys/web/example.py" in paths
    assert "src/qsys/live/example.py" in paths
    assert "scripts/run_monitor.py" not in paths
    assert verify(repo, sha, "web", release) == value


def test_storage_report_release_uses_its_own_entrypoint_and_ops_scan_root(
    tmp_path: Path,
) -> None:
    repo, sha = _repo(tmp_path)
    wheel = _wheel(
        tmp_path / "dist-storage" / "qsys-0.1.0-py3-none-any.whl",
        command="qsys-storage-report", entrypoint="qsys.ops.storage_report_cli:main",
    )
    release = tmp_path / "storage-release"

    value = build(
        repo, sha, "storage-report", wheel.parent, release,
        repository="owner/repo", run_id="789", run_attempt="1",
    )

    assert value["entrypoint"] == {
        "command": "qsys-storage-report", "target": "qsys.ops.storage_report_cli:main",
    }
    assert {record["path"].split("/")[2] for record in value["scan_files"]} == {"ops"}
    assert verify(repo, sha, "storage-report", release) == value


def test_daily_digest_release_uses_its_own_entrypoint_and_read_scan_roots(
    tmp_path: Path,
) -> None:
    repo, sha = _repo(tmp_path)
    wheel = _wheel(
        tmp_path / "dist-digest" / "qsys-0.1.0-py3-none-any.whl",
        command="qsys-daily-digest", entrypoint="qsys.ops.daily_digest_cli:main",
    )
    release = tmp_path / "digest-release"

    value = build(
        repo, sha, "daily-digest", wheel.parent, release,
        repository="owner/repo", run_id="790", run_attempt="1",
    )

    assert value["entrypoint"] == {
        "command": "qsys-daily-digest", "target": "qsys.ops.daily_digest_cli:main",
    }
    assert {record["path"].split("/")[2] for record in value["scan_files"]} == {
        "data", "live", "ops", "paper",
    }
    assert verify(repo, sha, "daily-digest", release) == value


def test_health_observer_release_binds_its_resident_dependency_roots(
    tmp_path: Path,
) -> None:
    repo, sha = _repo(tmp_path)
    wheel = _wheel(
        tmp_path / "dist-observer" / "qsys-0.1.0-py3-none-any.whl",
        command="qsys-health-observer", entrypoint="qsys.ops.health_observer_cli:main",
    )
    release = tmp_path / "observer-release"

    value = build(
        repo, sha, "health-observer", wheel.parent, release,
        repository="owner/repo", run_id="791", run_attempt="1",
    )

    assert value["entrypoint"] == {
        "command": "qsys-health-observer",
        "target": "qsys.ops.health_observer_cli:main",
    }
    assert {record["path"].split("/")[2] for record in value["scan_files"]} == {
        "data", "live", "ops", "paper", "web",
    }
    assert verify(repo, sha, "health-observer", release) == value


def test_daily_archive_release_binds_data_code_and_windows_downloader(
    tmp_path: Path,
) -> None:
    repo, sha = _repo(tmp_path)
    wheel = _wheel(
        tmp_path / "dist-archive" / "qsys-0.1.0-py3-none-any.whl",
        command="qsys-daily-archive", entrypoint="qsys.data.daily_archive_cli:main",
    )
    release = tmp_path / "archive-release"

    value = build(
        repo, sha, "daily-archive", wheel.parent, release,
        repository="owner/repo", run_id="792", run_attempt="1",
    )

    paths = {record["path"] for record in value["scan_files"]}
    assert value["entrypoint"] == {
        "command": "qsys-daily-archive", "target": "qsys.data.daily_archive_cli:main",
    }
    assert "scripts/win_download_minutes.py" in paths
    assert "scripts/win_download_auction_ticks.py" in paths
    assert not any(
        path.startswith("scripts/") and path not in {"scripts/win_download_minutes.py",
                                                       "scripts/win_download_auction_ticks.py"}
        for path in paths
    )
    assert (release / "source/scripts/win_download_minutes.py").is_file()
    assert verify(repo, sha, "daily-archive", release) == value


def test_premarket_limits_release_binds_writer_and_redis_dependencies(
    tmp_path: Path,
) -> None:
    repo, sha = _repo(tmp_path)
    wheel = _wheel(
        tmp_path / "dist-premarket" / "qsys-0.1.0-py3-none-any.whl",
        command="qsys-premarket-limits",
        entrypoint="qsys.data.premarket_limits_cli:main",
    )
    release = tmp_path / "premarket-release"

    value = build(
        repo, sha, "premarket-limits", wheel.parent, release,
        repository="owner/repo", run_id="793", run_attempt="1",
    )

    assert value["entrypoint"] == {
        "command": "qsys-premarket-limits",
        "target": "qsys.data.premarket_limits_cli:main",
    }
    assert {record["path"].split("/")[2] for record in value["scan_files"]} == {
        "core", "data", "live", "ops",
    }
    assert verify(repo, sha, "premarket-limits", release) == value


def test_tushare_sync_release_binds_writer_and_late_minute_downloader(
    tmp_path: Path,
) -> None:
    repo, sha = _repo(tmp_path)
    wheel = _wheel(
        tmp_path / "dist-tushare-sync" / "qsys-0.1.0-py3-none-any.whl",
        command="qsys-tushare-sync",
        entrypoint="qsys.data.tushare_sync_cli:main",
    )
    release = tmp_path / "tushare-sync-release"

    value = build(
        repo, sha, "tushare-sync", wheel.parent, release,
        repository="owner/repo", run_id="794", run_attempt="1",
    )

    paths = {record["path"] for record in value["scan_files"]}
    assert value["entrypoint"] == {
        "command": "qsys-tushare-sync",
        "target": "qsys.data.tushare_sync_cli:main",
    }
    assert "scripts/win_download_minutes.py" in paths
    assert "scripts/win_download_auction_ticks.py" in paths
    assert "scripts/win_download_dividends.py" in paths
    assert not any(
        path.startswith("scripts/") and path not in {"scripts/win_download_minutes.py",
                                                       "scripts/win_download_auction_ticks.py",
                                                       "scripts/win_download_dividends.py"}
        for path in paths
    )
    assert (release / "source/scripts/win_download_minutes.py").is_file()
    assert (release / "source/scripts/win_download_dividends.py").is_file()
    assert verify(repo, sha, "tushare-sync", release) == value


def test_reboot_release_binds_control_plane_members_and_entrypoint(tmp_path):
    repo, sha = _repo(tmp_path)
    wheel = _wheel(tmp_path / "dist/qsys-0.1.0-py3-none-any.whl",
                   command="qsys-reboot-drill", entrypoint="qsys.ops.reboot_drill_cli:main")
    release = tmp_path / "release"
    value = build(repo, sha, "reboot-drill", wheel.parent, release,
                  repository="owner/repo", run_id="123", run_attempt="1")
    assert value["entrypoint"] == {"command": "qsys-reboot-drill", "target": "qsys.ops.reboot_drill_cli:main"}
    assert verify(repo, sha, "reboot-drill", release) == value
    # The packaged control plane, not shared scripts, must be hash-bound.
    (release / "source/src/qsys/ops/example.py").write_text("CHANGED = True\n")
    with pytest.raises(ValueError):
        verify(repo, sha, "reboot-drill", release)


def test_live_release_binds_entrypoint_and_runtime_sources(tmp_path):
    repo, sha = _repo(tmp_path)
    (repo/'src/qsys/agent').mkdir()
    (repo/'src/qsys/agent/example.py').write_text('AGENT=1\n')
    (repo/'strategies').mkdir()
    (repo/'strategies/structural_v1_2.py').write_text('STRATEGY=1\n')
    subprocess.run(['git','add','.'],cwd=repo,check=True)
    subprocess.run(['git','-c','user.name=test','-c','user.email=test@example.invalid','commit','-m','fixture live sources'],cwd=repo,check=True,capture_output=True)
    sha=subprocess.check_output(['git','rev-parse','HEAD'],cwd=repo,text=True).strip()
    wheel = _wheel(tmp_path/'dist/qsys-0.1.0-py3-none-any.whl',
                   command='qsys-live-core',entrypoint='qsys.live.core_cli:main')
    release = tmp_path/'live-release'
    value = build(repo,sha,'live-core',wheel.parent,release,repository='owner/repo',run_id='123',run_attempt='1')
    assert value['entrypoint']=={'command':'qsys-live-core','target':'qsys.live.core_cli:main'}
    assert verify(repo,sha,'live-core',release)==value
    (release/'source/src/qsys/live/example.py').write_text('CHANGED=True\n')
    with pytest.raises(ValueError):
        verify(repo,sha,'live-core',release)
