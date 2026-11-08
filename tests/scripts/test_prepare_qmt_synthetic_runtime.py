import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

from scripts.prepare_qmt_synthetic_runtime import (
    FIXTURE_TARGET,
    REBOOT_COORDINATOR_SOURCE,
    REBOOT_COORDINATOR_TARGET,
    REBOOT_DRIVER_SOURCE,
    REBOOT_DRIVER_TARGET,
    RUNTIME_FILES,
    stage_reboot_bundle_files,
    stage_runtime_files,
    verify_reboot_bundle_files,
    verify_runtime_files,
)


SOURCE = Path(__file__).resolve().parents[2] / "docs" / "reference"
RUN = "0123456789abcdef0123456789abcdef"


def test_stage_uses_exact_runtime_names_and_imports_from_final_directory(tmp_path):
    receipt = stage_runtime_files(SOURCE, tmp_path)

    assert set(receipt) == {*RUNTIME_FILES, "runtime_import"}
    assert {path.name for path in tmp_path.iterdir()} == set(RUNTIME_FILES)
    assert receipt["runtime_import"] == "verified"
    for name in RUNTIME_FILES:
        payload = (SOURCE / name).read_bytes()
        assert (tmp_path / name).read_bytes() == payload
        assert receipt[name] == hashlib.sha256(payload).hexdigest()


def test_renamed_protocol_copy_cannot_satisfy_runtime_contract(tmp_path):
    (tmp_path / "qmt-synthetic-intent-port.py").write_bytes(
        (SOURCE / "qmt-synthetic-intent-port.py").read_bytes()
    )
    (tmp_path / "candidate-default.py").write_bytes(
        (SOURCE / "qmt-synthetic-intent-protocol.py").read_bytes()
    )

    with pytest.raises(ValueError, match="qmt-synthetic-intent-protocol.py"):
        verify_runtime_files(SOURCE, tmp_path)


def test_runtime_import_itself_fails_when_exact_protocol_name_is_missing(tmp_path):
    port = tmp_path / "qmt-synthetic-intent-port.py"
    port.write_bytes((SOURCE / port.name).read_bytes())
    spec = importlib.util.spec_from_file_location("missing_protocol_port", port)
    module = importlib.util.module_from_spec(spec)

    with pytest.raises(FileNotFoundError, match="qmt-synthetic-intent-protocol.py"):
        spec.loader.exec_module(module)


def test_stage_refuses_to_overwrite_any_existing_runtime_file(tmp_path):
    existing = tmp_path / RUNTIME_FILES[1]
    existing.write_text("preserve-me", encoding="utf-8")

    with pytest.raises(FileExistsError):
        stage_runtime_files(SOURCE, tmp_path)

    assert existing.read_text(encoding="utf-8") == "preserve-me"
    assert not (tmp_path / RUNTIME_FILES[0]).exists()


def test_verify_rejects_changed_runtime_bytes(tmp_path):
    for name in RUNTIME_FILES:
        (tmp_path / name).write_bytes((SOURCE / name).read_bytes())
    (tmp_path / RUNTIME_FILES[1]).write_bytes(b"changed")

    with pytest.raises(ValueError, match="differs from reviewed source"):
        verify_runtime_files(SOURCE, tmp_path)


def test_verify_rejects_unreviewed_extra_file(tmp_path):
    for name in RUNTIME_FILES:
        (tmp_path / name).write_bytes((SOURCE / name).read_bytes())
    (tmp_path / "candidate-default.py").write_bytes(
        (SOURCE / "qmt-synthetic-intent-protocol.py").read_bytes()
    )

    with pytest.raises(ValueError, match="only the exact reviewed file set"):
        verify_runtime_files(SOURCE, tmp_path)


def test_verify_rejects_symlink_target_directory(tmp_path):
    actual = tmp_path / "actual"
    actual.mkdir()
    link = tmp_path / "runtime-link"
    link.symlink_to(actual, target_is_directory=True)

    with pytest.raises(ValueError, match="non-symlink directory"):
        verify_runtime_files(SOURCE, link)


def test_stage_reboot_bundle_uses_reviewed_driver_and_runtime_shape(tmp_path):
    receipt = stage_reboot_bundle_files(SOURCE, tmp_path, RUN, "rehearsal")

    assert (tmp_path / REBOOT_DRIVER_TARGET).read_bytes() == (
        SOURCE / REBOOT_DRIVER_SOURCE).read_bytes()
    assert {path.name for path in (tmp_path / "runtime").iterdir()} == set(RUNTIME_FILES)
    assert receipt["driver_compile"] == "verified"
    assert receipt["runtime/runtime_import"] == "verified"
    assert receipt[REBOOT_DRIVER_TARGET] == hashlib.sha256(
        (SOURCE / REBOOT_DRIVER_SOURCE).read_bytes()).hexdigest()


@pytest.mark.parametrize(
    "existing",
    [REBOOT_DRIVER_TARGET, REBOOT_COORDINATOR_TARGET, FIXTURE_TARGET, "runtime"],
)
def test_stage_reboot_bundle_refuses_to_overwrite_partial_state(tmp_path, existing):
    path = tmp_path / existing
    if existing == "runtime":
        path.mkdir()
    else:
        path.write_text("preserve", encoding="utf-8")

    with pytest.raises(FileExistsError, match="overwrite"):
        stage_reboot_bundle_files(SOURCE, tmp_path, RUN, "rehearsal")

    assert path.exists()


def test_verify_reboot_bundle_rejects_changed_driver(tmp_path):
    stage_reboot_bundle_files(SOURCE, tmp_path, RUN, "rehearsal")
    (tmp_path / REBOOT_DRIVER_TARGET).write_text("changed", encoding="utf-8")

    with pytest.raises(ValueError, match="driver differs"):
        verify_reboot_bundle_files(SOURCE, tmp_path, RUN, "rehearsal")


@pytest.mark.parametrize("mode", ["reboot", "rehearsal"])
def test_stage_reboot_bundle_binds_coordinator_and_fixture(tmp_path, mode):
    receipt = stage_reboot_bundle_files(SOURCE, tmp_path, RUN, mode)

    assert (tmp_path / REBOOT_COORDINATOR_TARGET).read_bytes() == (
        SOURCE / REBOOT_COORDINATOR_SOURCE).read_bytes()
    fixture = json.loads((tmp_path / FIXTURE_TARGET).read_text(encoding="ascii"))
    assert fixture == {
        "coordinator_task": f"qsys-issue601-{mode}-coordinate-{RUN[:8]}",
        "first": RUN,
        "mode": mode,
    }
    assert receipt["coordinator_task"] == fixture["coordinator_task"]
    assert {path.name for path in tmp_path.iterdir()} == {
        REBOOT_DRIVER_TARGET, REBOOT_COORDINATOR_TARGET, FIXTURE_TARGET, "runtime"}


@pytest.mark.parametrize(
    ("run_id", "mode"),
    [("", "reboot"), (RUN.upper(), "reboot"), (RUN[:-1], "reboot"), (RUN, "live")],
)
def test_stage_reboot_bundle_rejects_bad_binding_before_writing(tmp_path, run_id, mode):
    with pytest.raises(ValueError):
        stage_reboot_bundle_files(SOURCE, tmp_path, run_id, mode)

    assert list(tmp_path.iterdir()) == []


def test_verify_reboot_bundle_rejects_other_run_or_mode(tmp_path):
    stage_reboot_bundle_files(SOURCE, tmp_path, RUN, "rehearsal")

    with pytest.raises(ValueError, match="fixture.json"):
        verify_reboot_bundle_files(SOURCE, tmp_path, "f" * 32, "rehearsal")
    with pytest.raises(ValueError, match="fixture.json"):
        verify_reboot_bundle_files(SOURCE, tmp_path, RUN, "reboot")


def test_verify_reboot_bundle_rejects_changed_coordinator(tmp_path):
    stage_reboot_bundle_files(SOURCE, tmp_path, RUN, "reboot")
    (tmp_path / REBOOT_COORDINATOR_TARGET).write_text("changed", encoding="utf-8")

    with pytest.raises(ValueError, match="coordinator differs"):
        verify_reboot_bundle_files(SOURCE, tmp_path, RUN, "reboot")


def test_verify_reboot_bundle_requires_fixture(tmp_path):
    stage_reboot_bundle_files(SOURCE, tmp_path, RUN, "reboot")
    (tmp_path / FIXTURE_TARGET).unlink()

    with pytest.raises(ValueError, match="regular file"):
        verify_reboot_bundle_files(SOURCE, tmp_path, RUN, "reboot")
