"""统一错误处理。

为什么要有这一层：
1. 业务代码可以 `raise NotFoundError("剧集不存在")`，不必关心 HTTP 状态码怎么组织；
2. 所有错误返回同一种 JSON 结构 {"error": {"code", "message"}}，前端只写一套解析逻辑；
3. 内部异常不会把堆栈泄露给调用方（安全），但日志里完整保留（可排查）。
"""

import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)


class AppError(Exception):
    """所有业务异常的基类。"""

    status_code: int = 400
    code: str = "app_error"

    def __init__(self, message: str, *, code: str | None = None, status_code: int | None = None):
        super().__init__(message)
        self.message = message
        if code is not None:
            self.code = code
        if status_code is not None:
            self.status_code = status_code


class NotFoundError(AppError):
    status_code = 404
    code = "not_found"


class ConflictError(AppError):
    status_code = 409
    code = "conflict"


class LLMError(AppError):
    """调用大模型失败（网络、鉴权、返回格式不对）。属于上游依赖问题，用 502。"""

    status_code = 502
    code = "llm_error"


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _handle_app_error(request: Request, exc: AppError) -> JSONResponse:
        logger.warning("业务异常 %s %s -> %s(%s): %s", request.method, request.url.path, exc.code, exc.status_code, exc.message)
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": {"code": exc.code, "message": exc.message}},
        )

    @app.exception_handler(Exception)
    async def _handle_unexpected(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("未捕获异常 %s %s", request.method, request.url.path)
        return JSONResponse(
            status_code=500,
            content={"error": {"code": "internal_error", "message": "服务器内部错误"}},
        )
