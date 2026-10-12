"""pytest 全局夹具。

这里演示了后端测试的核心手法：**用依赖覆盖把真实数据库换成临时库**。
好处是测试之间互不污染、跑完即删（tmp_path 由 pytest 自动清理），
而且完全不需要你本机装 MySQL。
"""

import os

# 必须在导入 app 之前设置：配置是单例，一旦实例化就不会再读环境变量
os.environ.setdefault("AUTO_CREATE_TABLES", "false")
os.environ.setdefault("LLM_PROVIDER", "mock")
os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite:///./_test_placeholder.db")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from app.db.base import Base  # noqa: E402
from app.db.session import get_db  # noqa: E402
from app.main import create_app  # noqa: E402


@pytest.fixture()
def client(tmp_path):
    """每个测试用例一个全新的空数据库 + 独立的 app 实例。"""
    engine = create_engine(
        f"sqlite+pysqlite:///{tmp_path / 'test.db'}",
        connect_args={"check_same_thread": False},
    )
    TestingSession = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    Base.metadata.create_all(engine)

    def override_get_db():
        with TestingSession() as session:
            yield session

    app = create_app()
    app.dependency_overrides[get_db] = override_get_db

    # raise_server_exceptions=False：让未捕获异常也走统一错误处理器，返回 500 JSON
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client

    Base.metadata.drop_all(engine)
    engine.dispose()
