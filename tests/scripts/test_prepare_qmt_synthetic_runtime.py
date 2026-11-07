import hashlib
import importlib.util
from pathlib import Path

import pytest

from scripts.prepare_qmt_synthetic_runtime import (
    RUNTIME_FILES,
    stage_runtime_files,
    verify_runtime_files,
)


SOURCE = Path(__file__).resolve().parents[2] / "docs" / "reference"


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
