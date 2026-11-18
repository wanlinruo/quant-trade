"""新后端骨架：契约基类、时间约定与统一错误格式（#662，总路线 v2 第 3 节）。"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError

from qsys.webapp.contracts.base import Contract, Timestamp
from qsys.webapp.errors import install_error_handlers


class Sample(Contract):
    at: Timestamp


def test_naive_local_time_serialized_with_shanghai_offset():
    s = Sample(at=datetime(2026, 11, 18, 10, 42, 5))
    assert s.model_dump(mode="json")["at"] == "2026-11-18T10:42:05+08:00"


def test_aware_time_converted_not_relabelled():
    s = Sample(at=datetime(2026, 11, 18, 10, 0, tzinfo=timezone.utc))
    assert s.model_dump(mode="json")["at"] == "2026-11-18T18:00:00+08:00"


def test_contract_forbids_extra():
    with pytest.raises(ValidationError):
        Sample(at=datetime(2026, 1, 1), extra=1)


def _app() -> TestClient:
    app = FastAPI()
    install_error_handlers(app)

    @app.get("/api/app/boom")
    def boom():
        raise RuntimeError("/secret/path")

    @app.get("/api/app/missing")
    def missing():
        raise HTTPException(status_code=404, detail="策略不存在")

    @app.get("/api/app/typed")
    def typed(n: int):
        return {"n": n}

    @app.get("/legacy/missing")
    def legacy():
        raise HTTPException(status_code=404, detail="old")

    return TestClient(app, raise_server_exceptions=False)


def test_http_error_body_shape():
    r = _app().get("/api/app/missing")
    assert r.status_code == 404
    assert r.json() == {"error": {"code": "http_404", "message": "策略不存在"}}


def test_validation_error_body():
    r = _app().get("/api/app/typed", params={"n": "x"})
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "invalid_request"


def test_unhandled_error_is_generic():
    r = _app().get("/api/app/boom")
    assert r.status_code == 500
    assert r.json() == {"error": {"code": "internal_error", "message": "服务内部错误"}}
    assert "secret" not in r.text


def test_non_app_paths_keep_default_errors():
    r = _app().get("/legacy/missing")
    assert r.status_code == 404 and r.json() == {"detail": "old"}
