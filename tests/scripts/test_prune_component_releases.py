"""#126 组件旧 release 清理：保留 current 与回退目标，任何引用都拒绝。"""
import hashlib
import json
import os
import sys
from argparse import Namespace
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import prune_component_releases as P  # noqa: E402

A, B, C, D = ("a" * 40, "b" * 40, "c" * 40, "d" * 40)


@pytest.fixture
def layout(tmp_path):
    root = tmp_path / "components" / "web"
    for sha in (A, B, C):
        (root / "releases" / sha / "venv").mkdir(parents=True)
        (root / "releases" / sha / "venv" / "file").write_text(sha)
    (root / "current").symlink_to(root / "releases" / C)
    specs = [P.Spec("web", root / "releases", root / "current")]
    return tmp_path, specs


def _cands(result):
    return sorted(c["release"] for c in result["candidates"])


def test_keeps_current_and_rollback_only(layout):
    _, specs = layout
    result = P.plan(specs, {"web": B})
    assert _cands(result) == [A]
    assert {k["role"] for k in result["kept"]} == {"current", "rollback"}


def test_component_without_successful_record_is_skipped_entirely(layout):
    _, specs = layout
    result = P.plan(specs, {})
    assert result["candidates"] == [] and result["skipped"][0]["component"] == "web"


def test_latest_rollbacks_uses_most_recent_successful_production_record(tmp_path):
    def rec(name, status, env, at, target):
        path = tmp_path / name / "record.json"
        path.parent.mkdir()
        path.write_text(json.dumps({"status": status, "environment": env, "completed_at": at,
                                    "components": ["web"], "rollback_targets": {"web": target}}))
    rec("r1", "success", "production", "2026-11-12T00:00:00Z", A)
    rec("r2", "success", "production", "2026-11-14T00:00:00Z", B)
    rec("r3", "failure", "production", "2026-11-15T00:00:00Z", D)
    rec("r4", "success", "testing", "2026-11-16T00:00:00Z", D)
    assert P.latest_rollbacks(tmp_path) == {"web": B}


def test_references_in_config_and_symlink_are_detected(layout, tmp_path):
    root, specs = layout
    target = str((specs[0].release_root / A).resolve())
    etc = tmp_path / "etc"; etc.mkdir()
    (etc / "unit.service").write_text(f"ExecStart={target}/venv/bin/x\n")
    links = tmp_path / "links"; links.mkdir()
    os.symlink(target, links / "old")
    refs = P.references([target], proc=tmp_path / "noproc", config_roots=(etc,), link_roots=(links,))
    assert refs["config"] and refs["symlink"] and refs["process"] == []


def test_inode_estimate_excludes_external_hardlinks(layout, tmp_path):
    _, specs = layout
    target = specs[0].release_root / A
    os.link(target / "venv" / "file", tmp_path / "outside-link")
    estimate = P.inode_estimate([str(target)])
    assert estimate["external_linked_bytes"] > 0
    assert estimate["estimated_free_bytes"] == estimate["union_bytes"] - estimate["external_linked_bytes"]


def _apply_args(tmp_path, plan_path, sha):
    return Namespace(plan=str(plan_path), plan_sha256=sha, run_dir=str(tmp_path / "run"),
                     whitelist=None, records=None)


def _write_plan(tmp_path, specs, rollbacks):
    out = tmp_path / "plan.json"
    rc = P.cmd_plan(Namespace(out=str(out), whitelist=None, records=None), specs, rollbacks)
    return out, hashlib.sha256(out.read_bytes()).hexdigest(), rc


@pytest.fixture
def quiet_refs(monkeypatch):
    monkeypatch.setattr(P, "references", lambda paths, **_: {"process": [], "config": [], "symlink": []})
    monkeypatch.setattr(P, "identity", lambda specs: ["fixed"])


def test_apply_deletes_confirmed_candidates_only(layout, tmp_path, quiet_refs):
    _, specs = layout
    out, sha, rc = _write_plan(tmp_path, specs, {"web": B})
    assert rc == 0
    assert P.cmd_apply(_apply_args(tmp_path, out, sha), specs, {"web": B}) == 0
    assert sorted(p.name for p in specs[0].release_root.iterdir()) == [B, C]
    assert (tmp_path / "run" / "manifest-inodes.tsv").read_text().strip()


def test_apply_rejects_plan_hash_mismatch(layout, tmp_path, quiet_refs):
    _, specs = layout
    out, _, _ = _write_plan(tmp_path, specs, {"web": B})
    assert P.cmd_apply(_apply_args(tmp_path, out, "0" * 64), specs, {"web": B}) == 2
    assert (specs[0].release_root / A).exists()


def test_apply_rejects_when_state_changed_since_confirmation(layout, tmp_path, quiet_refs):
    _, specs = layout
    out, sha, _ = _write_plan(tmp_path, specs, {"web": B})
    # 确认后又发布了一次：回退目标变成 A，A 不该再删
    assert P.cmd_apply(_apply_args(tmp_path, out, sha), specs, {"web": A}) == 2
    assert (specs[0].release_root / A).exists()


def test_apply_rejects_when_references_appear(layout, tmp_path, monkeypatch):
    _, specs = layout
    monkeypatch.setattr(P, "identity", lambda specs: ["fixed"])
    monkeypatch.setattr(P, "references", lambda paths, **_: {"process": [], "config": [], "symlink": []})
    out, sha, _ = _write_plan(tmp_path, specs, {"web": B})
    monkeypatch.setattr(P, "references", lambda paths, **_: {"process": ["123"], "config": [], "symlink": []})
    assert P.cmd_apply(_apply_args(tmp_path, out, sha), specs, {"web": B}) == 2
    assert (specs[0].release_root / A).exists()


def test_load_specs_rejects_unknown_shape(tmp_path):
    path = tmp_path / "w.json"
    path.write_text(json.dumps({"web": {"release_root": "/x", "current": "/y"}}))
    with pytest.raises(ValueError):
        P.load_specs(path)
