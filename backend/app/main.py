"""应用入口：把各层组装起来。

create_app() 写成工厂函数而不是直接建全局 app 的好处：
测试可以造出多个互不干扰的 app 实例，并覆盖依赖。
`app = create_app()` 则是给 uvicorn 用的（uvicorn app.main:app）。
"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.v1 import api_router
from app.core.config import get_settings
from app.core.errors import register_exception_handlers
from app.core.logging import setup_logging
from app.db import models  # noqa: F401  必须导入，表才会注册到 Base.metadata
from app.db.base import Base
from app.db.session import get_engine

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """启动/关闭钩子。

    放在 lifespan 里而不是模块顶层：这样建表动作只在"服务真正启动"时发生，
    导入模块（比如跑测试、跑脚本）不会有意外的副作用。
    """
    settings = get_settings()
    setup_logging()

    if settings.auto_create_tables:
        Base.metadata.create_all(get_engine())
        logger.info("已根据模型创建/校验数据表")

    logger.info("%s 启动完成 env=%s llm=%s", settings.app_name, settings.env, settings.llm_provider)
    yield
    logger.info("%s 已关闭", settings.app_name)


def create_app() -> FastAPI:
    settings = get_settings()

    app = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        description="AI 短剧创作服务：管理短剧/剧集/分镜，并调用大模型生成剧本。",
        lifespan=lifespan,
    )

    register_exception_handlers(app)
    app.include_router(api_router, prefix=settings.api_prefix)

    return app


app = create_app()
