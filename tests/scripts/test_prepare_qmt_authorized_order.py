import importlib.util
import json
from datetime import datetime, timedelta, timezone

import pytest

from scripts.prepare_qmt_authorized_order import render_enabled, stage, verify

RUN = "0123456789abcdef0123456789abcdef"
ACCOUNT = "777888999"


def window():
    now = datetime.now(timezone.utc)
    return ((now - timedelta(minutes=1)).isoformat(), (now + timedelta(minutes=59)).isoformat())


def account_file(tmp_path):
    path = tmp_path / "account.txt"
    path.write_text(ACCOUNT + "\n", encoding="ascii")
    return path


def staged(tmp_path, places=("a:buy:510300.SH:100:3.900:resting", "b:buy:510300.SH:100:4.010:fill")):
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    receipt = stage(bundle, RUN, account_file(tmp_path), list(places), *window())
    return bundle, receipt


def test_stage_builds_bound_bundle_without_printing_secrets(tmp_path):
    bundle, receipt = staged(tmp_path)

    text = json.dumps(receipt)
    assert ACCOUNT not in text
    assert (bundle / "key.hex").read_text() not in text
    assert receipt["expect"] == {"q601-01234567-a": "resting", "q601-01234567-b": "fill"}
    assert [s["action"] for s in receipt["steps"]] == ["place", "cancel", "place", "cancel"]
    spec = importlib.util.spec_from_file_location("enabled", bundle / "executor-enabled.py")
    enabled = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(enabled)
    assert enabled.ENABLED is True and enabled.ACCOUNT_ID == ACCOUNT and enabled.RUN_ID == RUN
    assert enabled.AUTHORIZED_PLAN == (bundle / "plan.json").read_text()
    assert enabled.KEY_HEX == (bundle / "key.hex").read_text()
    assert enabled.validate_plan(enabled.AUTHORIZED_PLAN, RUN)["steps"][0]["limit_price"] == 3.9


def test_stage_refuses_to_overwrite(tmp_path):
    bundle, _ = staged(tmp_path)
    with pytest.raises(FileExistsError):
        stage(bundle, RUN, account_file(tmp_path), ["a:buy:510300.SH:100:3.900:resting"], *window())


@pytest.mark.parametrize("place", [
    "a:buy:920001.SH:100:3.900:resting",
    "a:buy:510300.SZ:100:3.900:resting",
    "a:buy:510300.SH:200:3.900:resting",
    "a:buy:510300.SH:100:12.000:fill",
    "a:short:510300.SH:100:3.900:fill",
])
def test_stage_rejects_out_of_bounds_orders_before_writing(tmp_path, place):
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    with pytest.raises(ValueError):
        stage(bundle, RUN, account_file(tmp_path), [place], *window())
    assert list(bundle.iterdir()) == []


def test_verify_detects_tampering(tmp_path):
    bundle, _ = staged(tmp_path)
    enabled = bundle / "executor-enabled.py"
    enabled.write_text(enabled.read_text().replace("MAX_QTY = 100", "MAX_QTY = 1000"))
    with pytest.raises(ValueError, match="literal binding"):
        verify(bundle, RUN, account_file(tmp_path))

    second = tmp_path / "second"
    second.mkdir()
    bundle, _ = staged(second)
    plan = bundle / "plan.json"
    plan.write_text(plan.read_text().replace("3.9", "3.8"))
    with pytest.raises(ValueError):
        verify(bundle, RUN, account_file(tmp_path))


def test_verify_rejects_other_account_or_run(tmp_path):
    bundle, _ = staged(tmp_path)
    other = tmp_path / "other.txt"
    other.write_text("987654321", encoding="ascii")
    with pytest.raises(ValueError, match="literal binding"):
        verify(bundle, RUN, other)
    with pytest.raises(ValueError):
        verify(bundle, "f" * 32, account_file(tmp_path))


def test_render_requires_each_literal_exactly_once():
    with pytest.raises(ValueError, match="exactly one line"):
        render_enabled("ENABLED = False\n", RUN, ACCOUNT, "0" * 64, "{}")


def test_account_file_must_be_numeric(tmp_path):
    bad = tmp_path / "account.txt"
    bad.write_text("acct-1", encoding="ascii")
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    with pytest.raises(ValueError, match="numeric account"):
        stage(bundle, RUN, bad, ["a:buy:510300.SH:100:3.900:resting"], *window())
