"""应用入口：把各层组装起来。

create_app() 写成工厂函数而不是直接建全局 app 的好处：
测试可以造出多个互不干扰的 app 实例，并覆盖依赖。
`app = create_app()` 则是给 uvicorn 用的（uvicorn app.main:app）。
"""

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.openapi.docs import get_redoc_html, get_swagger_ui_html
from fastapi.staticfiles import StaticFiles

from app.api.v1 import api_router
from app.core.config import get_settings
from app.core.errors import register_exception_handlers
from app.core.logging import setup_logging
from app.db import models  # noqa: F401  必须导入，表才会注册到 Base.metadata
from app.db.base import Base
from app.db.session import get_engine

logger = logging.getLogger(__name__)

# 静态资源目录（存的是 Swagger UI / ReDoc 的前端文件）
STATIC_DIR = Path(__file__).parent / "static"


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


def _register_docs_routes(app: FastAPI, app_name: str) -> None:
    """把 /docs 和 /redoc 换成"使用本地静态资源"的版本。

    为什么必须这么做（真实踩过的坑）：
    FastAPI 默认的 /docs 页面会去 cdn.jsdelivr.net 加载 swagger-ui-bundle.js。
    CDN 一旦连不上（国内网络、公司内网、离线环境都会遇到），
    结果就是：HTML 返回 200，但页面一片空白，而且日志里看不出任何错误。
    把这两个文件放进项目自己托管后，离线环境也能正常打开接口文档。
    """

    @app.get("/docs", include_in_schema=False)
    async def swagger_ui_html():
        return get_swagger_ui_html(
            openapi_url=app.openapi_url,
            title=f"{app_name} - Swagger UI",
            swagger_js_url="/static/swagger-ui/swagger-ui-bundle.js",
            swagger_css_url="/static/swagger-ui/swagger-ui.css",
            swagger_favicon_url="/static/swagger-ui/favicon-32x32.png",
        )

    @app.get("/redoc", include_in_schema=False)
    async def redoc_html():
        return get_redoc_html(
            openapi_url=app.openapi_url,
            title=f"{app_name} - ReDoc",
            redoc_js_url="/static/redoc/redoc.standalone.js",
            redoc_favicon_url="/static/swagger-ui/favicon-32x32.png",
        )


def create_app() -> FastAPI:
    settings = get_settings()

    app = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        description="AI 短剧创作服务：管理短剧/剧集/分镜，并调用大模型生成剧本。",
        lifespan=lifespan,
        # 关掉自带的文档路由（它们依赖境外 CDN），下面用自己的版本替换
        docs_url=None,
        redoc_url=None,
    )

    # 静态资源：把 /static/xxx 映射到 app/static/xxx 这些文件
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    register_exception_handlers(app)
    app.include_router(api_router, prefix=settings.api_prefix)
    _register_docs_routes(app, settings.app_name)

    return app


app = create_app()
