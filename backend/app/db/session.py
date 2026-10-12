"""数据库连接与会话管理。

三个关键概念（面试常问）：
- Engine：连接池 + 数据库方言，进程内一个就够，线程安全。
- Session：一次工作单元（unit of work），不是线程安全的，每个请求一个。
- get_db：FastAPI 依赖，请求开始时创建 Session，请求结束（含抛异常）自动关闭。
  用 yield 依赖的好处是"无论成功失败都会执行 finally"，不会泄漏连接。
"""

from collections.abc import Iterator
from functools import lru_cache

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    settings = get_settings()
    url = settings.database_url

    kwargs: dict = {
        "pool_pre_ping": True,  # 取连接前先探活，避免 MySQL 8 小时断连导致的偶发报错
    }
    if url.startswith("sqlite"):
        # SQLite 默认禁止跨线程使用连接，FastAPI 的线程池会踩到，必须关掉
        kwargs["connect_args"] = {"check_same_thread": False}

    return create_engine(url, **kwargs)


@lru_cache(maxsize=1)
def get_session_factory() -> sessionmaker[Session]:
    return sessionmaker(
        bind=get_engine(),
        autoflush=False,  # 不想在查询前被自动 flush 干扰，需要时显式 flush
        expire_on_commit=False,  # commit 后对象仍可读属性，否则序列化响应时会再查一次库
    )


def get_db() -> Iterator[Session]:
    """FastAPI 依赖：每个请求一个 Session。"""
    with get_session_factory()() as session:
        yield session
