from __future__ import annotations

from datetime import date

import base64
import binascii

from fastapi import APIRouter, HTTPException, Request

from qsys.ops.alert_events import now_shanghai
from qsys.webapp.contracts.alerts import AckInfo, AckRequest, AckResult, AlertCenter, AlertDetail, AlertsDisabled
from qsys.webapp.queries.alerts import AlertCenterQuery


def alerts_router(query: AlertCenterQuery | None) -> APIRouter:
    from qsys.webapp.app import no_store

    router = APIRouter()

    @router.get("/api/app/alerts", response_model=AlertCenter | AlertsDisabled)
    def center(day: date | None = None):
        return no_store(AlertsDisabled() if query is None else query.center(day))

    @router.get("/api/app/alerts/detail", response_model=AlertDetail)
    def detail(key: str, subject: str = "", day: date | None = None):
        d = None if query is None else query.detail(key, subject, day)
        if d is None:
            raise HTTPException(status_code=404, detail="告警不存在")
        return no_store(d)

    if query is not None and query.acks is not None:
        @router.post("/api/app/alerts/ack", status_code=201, response_model=AckResult)
        def ack(body: AckRequest, request: Request):
            d = query.detail(body.key, body.subject, None)
            if d is None:
                raise HTTPException(status_code=404, detail="告警不存在")
            if body.through != d.item.last_at:
                raise HTTPException(status_code=409, detail="该告警已有新的发生，请刷新后再确认")
            record = query.acks.ack(body.key, body.subject, by=basic_user(request), through=d.item.last_at,
                                    at=now_shanghai())
            query.clear()
            r = no_store(AckResult(ack=AckInfo(by=record.by, acked_at=record.acked_at, through=record.through)))
            r.status_code = 201
            return r

    return router


def basic_user(request: Request) -> str:
    """HTTP Basic 认证的用户名；拿不到为 unknown。只取用户名，不读取、不记录密码。"""
    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "basic" or not token:
        return "unknown"
    try:
        user = base64.b64decode(token, validate=True).decode("utf-8").split(":", 1)[0]
    except (binascii.Error, UnicodeDecodeError, ValueError):
        return "unknown"
    return user or "unknown"
