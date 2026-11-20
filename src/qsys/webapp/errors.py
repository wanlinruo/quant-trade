"""新接口的统一错误格式：{"error": {"code", "message"}}，不回显异常文本与路径。

只作用于 /api/app/ 下的路径；其他路径（旧页面）保持 FastAPI 默认行为。
"""

from __future__ import annotations

import structlog
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from fastapi.exception_handlers import http_exception_handler, request_validation_exception_handler

from qsys.webapp.contracts.base import ErrorBody, ErrorDetail

API_PREFIX = "/api/app/"
log = structlog.get_logger(__name__)


def _body(status: int, code: str, message: str) -> JSONResponse:
    body = ErrorBody(error=ErrorDetail(code=code, message=message))
    return JSONResponse(body.model_dump(), status_code=status, headers={"Cache-Control": "no-store"})


def _ours(request: Request) -> bool:
    return request.url.path.startswith(API_PREFIX)


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(StarletteHTTPException)
    async def on_http(request: Request, exc: StarletteHTTPException):
        if not _ours(request):
            return await http_exception_handler(request, exc)
        message = exc.detail if isinstance(exc.detail, str) else "请求失败"
        return _body(exc.status_code, f"http_{exc.status_code}", message)

    @app.exception_handler(RequestValidationError)
    async def on_invalid(request: Request, exc: RequestValidationError):
        if not _ours(request):
            return await request_validation_exception_handler(request, exc)
        return _body(422, "invalid_request", "请求参数不合法")

    @app.exception_handler(Exception)
    async def on_error(request: Request, exc: Exception):
        log.error("webapp.unhandled", path=request.url.path, error_type=type(exc).__name__, exc_info=exc)
        return _body(500, "internal_error", "服务内部错误")
