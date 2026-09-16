from __future__ import annotations

from scripts.run_agent import _load_qmt_runtime


def test_load_qmt_runtime_preserves_utf8_path(tmp_path) -> None:
    runtime = tmp_path / "runtime.yml"
    runtime.write_text(
        "mode: shadow\n"
        "qmt_account_id: '123456789'\n"
        'qmt_userdata_path: "C:\\\\交易终端\\\\userdata_mini"\n',
        encoding="utf-8",
    )

    account, path = _load_qmt_runtime(str(runtime))

    assert account == "123456789"
    assert path == r"C:\交易终端\userdata_mini"
