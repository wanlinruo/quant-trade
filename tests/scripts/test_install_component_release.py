from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from scripts import install_component_release as installer


def _spec(tmp_path: Path, sha: str) -> tuple[dict[str, object], Path, Path, Path]:
    release = tmp_path / "releases" / sha
    executable = release / "venv/bin/qsys-example"
    executable.parent.mkdir(parents=True)
    executable.write_text("#!/bin/sh\n", encoding="utf-8")
    executable.chmod(0o755)
    (release / "release.json").write_text("{}\n", encoding="utf-8")
    current = tmp_path / "component/current"
    dropin = tmp_path / "systemd/qsys-example.service.d/release.conf"
    spec: dict[str, object] = {
        "entrypoint": {"command": "qsys-example", "target": "qsys.example:main"},
        "scan_roots": ["src/qsys/example"],
        "release_root": str(tmp_path / "releases"),
        "current": str(current),
        "executable": "venv/bin/qsys-example",
        "unit": "qsys-example.service",
        "dropin": str(dropin),
        "state_directories": [{
            "path": str(tmp_path / "state"), "owner": "ubuntu",
            "group": "ubuntu", "mode": "0750",
        }],
        "seed": None,
        "dropin_lines": ["[Service]", "ExecStart=/component/current/qsys-example"],
    }
    return spec, release, current, dropin


def _fake_sudo(*command: str, check: bool = True):
    if command[0] == "chown" or command[:2] == ("systemctl", "daemon-reload"):
        return subprocess.CompletedProcess(command, 0, "", "")
    if command[0] == "install" and "-d" in command:
        Path(command[-1]).mkdir(parents=True, exist_ok=True)
    elif command[0] == "install":
        target = Path(command[-1])
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(command[-2], target)
    elif command[0] == "ln":
        target = Path(command[-1])
        target.parent.mkdir(parents=True, exist_ok=True)
        target.unlink(missing_ok=True)
        target.symlink_to(command[-2])
    elif command[0] == "mv":
        os.replace(command[-2], command[-1])
    elif command[0] == "rm":
        Path(command[-1]).unlink(missing_ok=True)
    else:
        raise AssertionError(command)
    return subprocess.CompletedProcess(command, 0, "", "")


def _prepare(monkeypatch, tmp_path: Path, sha: str):
    spec, release, current, dropin = _spec(tmp_path, sha)
    monkeypatch.setattr(
        installer, "load_component_releases", lambda repo: {"example": spec},
    )
    monkeypatch.setattr(installer, "verify", lambda *args: {})
    monkeypatch.setattr(installer, "_sudo", _fake_sudo)
    monkeypatch.setattr(
        installer, "_run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 3, "", ""),
    )
    return spec, release, current, dropin


def test_generic_installer_activates_whitelisted_release(monkeypatch, tmp_path: Path) -> None:
    sha = "a" * 40
    _, release, current, dropin = _prepare(monkeypatch, tmp_path, sha)

    assert installer.activate(tmp_path, sha, "example") == release

    assert current.resolve() == release.resolve()
    assert dropin.read_text(encoding="utf-8") == (
        "[Service]\nExecStart=/component/current/qsys-example\n"
    )


def test_generic_installer_rolls_back_pointer_and_dropin_after_activation_failure(
    monkeypatch, tmp_path: Path,
) -> None:
    sha = "b" * 40
    _, _, current, dropin = _prepare(monkeypatch, tmp_path, sha)
    previous = tmp_path / "releases" / ("a" * 40)
    previous.mkdir(parents=True)
    current.parent.mkdir(parents=True)
    current.symlink_to(previous)
    dropin.parent.mkdir(parents=True)
    dropin.write_text("old-dropin\n", encoding="utf-8")
    calls = 0

    def failing_sudo(*command: str, check: bool = True):
        nonlocal calls
        if command[:2] == ("systemctl", "daemon-reload"):
            calls += 1
            if calls == 1 and check:
                raise subprocess.CalledProcessError(1, command)
        return _fake_sudo(*command, check=check)

    monkeypatch.setattr(installer, "_sudo", failing_sudo)

    with pytest.raises(subprocess.CalledProcessError):
        installer.activate(tmp_path, sha, "example")

    assert current.resolve() == previous.resolve()
    assert dropin.read_text(encoding="utf-8") == "old-dropin\n"


def test_generic_installer_fails_before_activation_when_seed_is_missing(
    monkeypatch, tmp_path: Path,
) -> None:
    sha = "c" * 40
    spec, _, current, dropin = _prepare(monkeypatch, tmp_path, sha)
    spec["seed"] = {
        "source": "missing.json", "source_environment": "QSYS_TEST_SEED",
        "target": str(tmp_path / "state/seed.json"), "owner": "ubuntu",
        "group": "ubuntu", "mode": "0640",
    }

    with pytest.raises(ValueError, match="without seed"):
        installer.activate(tmp_path, sha, "example")

    assert not current.exists()
    assert not dropin.exists()


def test_generic_installer_rejects_repository_spec_drift(
    monkeypatch, tmp_path: Path,
) -> None:
    sha = "d" * 40
    spec, _, current, _ = _prepare(monkeypatch, tmp_path, sha)
    drifted = dict(spec)
    drifted["executable"] = "venv/bin/tampered"
    monkeypatch.setattr(
        installer, "load_component_releases_file",
        lambda path: {"example": drifted},
    )

    with pytest.raises(ValueError, match="differs from installed whitelist"):
        installer.activate(
            tmp_path, sha, "example", trusted_spec=tmp_path / "trusted.json",
        )

    assert not current.exists()


@pytest.mark.parametrize("unit", ["qsys-reboot-drill.service", "qsys-reboot-drill.timer"])
@pytest.mark.parametrize("state", ["active", "activating", "deactivating", ""])
def test_reboot_installer_final_idle_gate_prevents_switch(monkeypatch, tmp_path, unit, state):
    sha = "a" * 40
    spec, _, current, dropin = _prepare(monkeypatch, tmp_path, sha)
    spec["unit"] = "qsys-reboot-drill.service"
    monkeypatch.setattr(installer, "load_component_releases", lambda repo: {"reboot-drill": spec})
    def probe(command, **kwargs):
        if "show" in command:
            value = state if unit in command else "inactive"
            return subprocess.CompletedProcess(command, 0, value + "\n", "")
        return subprocess.CompletedProcess(command, 3, "", "")
    monkeypatch.setattr(installer, "_run", probe)
    with pytest.raises(ValueError, match="running or unknown"):
        installer.activate(tmp_path, sha, "reboot-drill")
    assert not current.exists()
    assert not dropin.exists()


@pytest.mark.parametrize("fail_reload", [False, True])
def test_reboot_switch_and_restore_never_start_or_stop_units(monkeypatch, tmp_path, fail_reload):
    sha = "a" * 40
    spec, release, current, dropin = _prepare(monkeypatch, tmp_path, sha)
    spec["unit"] = "qsys-reboot-drill.service"
    monkeypatch.setattr(installer, "load_component_releases", lambda repo: {"reboot-drill": spec})
    def probe(command, **kwargs):
        return subprocess.CompletedProcess(command, 0 if "show" in command else 3,
                                           "inactive\n" if "show" in command else "", "")
    monkeypatch.setattr(installer, "_run", probe)
    commands = []
    def sudo(*command, check=True):
        commands.append(command)
        if fail_reload and command == ("systemctl", "daemon-reload") and check:
            raise subprocess.CalledProcessError(1, command)
        return _fake_sudo(*command, check=check)
    monkeypatch.setattr(installer, "_sudo", sudo)
    if fail_reload:
        with pytest.raises(subprocess.CalledProcessError):
            installer.activate(tmp_path, sha, "reboot-drill")
        assert not current.exists()
        assert not dropin.exists()
    else:
        assert installer.activate(tmp_path, sha, "reboot-drill") == release
        assert current.resolve() == release
    assert not any("systemctl" in c and any(v in c for v in ("start", "stop", "restart")) for c in commands)


@pytest.mark.parametrize("directive", ["ExecStart", "User", "ReadWritePaths", "ExecStartPre"])
def test_later_platform_override_blocks_component_before_sudo(monkeypatch, tmp_path, directive):
    sha = "a" * 40
    spec, _, current, dropin = _prepare(monkeypatch, tmp_path, sha)
    dropin.parent.mkdir(parents=True)
    (dropin.parent / "zz-legacy.conf").write_text(f"[Service]\n{directive}=/shared/checkout\n")
    monkeypatch.setattr(installer, "_sudo", lambda *args, **kw: pytest.fail("sudo reached"))
    with pytest.raises(ValueError, match="later platform drop-in"):
        installer.activate(tmp_path, sha, "example")
    assert not current.exists()
    assert not dropin.exists()


def test_nonconflicting_later_platform_environment_is_allowed(monkeypatch, tmp_path):
    sha = "a" * 40
    _, release, _, dropin = _prepare(monkeypatch, tmp_path, sha)
    dropin.parent.mkdir(parents=True)
    (dropin.parent / "zz-market.conf").write_text("[Service]\nEnvironment=MARKET=canonical\n")
    assert installer.activate(tmp_path, sha, "example") == release
