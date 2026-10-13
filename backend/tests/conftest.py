"""pytest 全局夹具。

这里演示了后端测试的核心手法：**用依赖覆盖把真实数据库换成临时库**。
好处是测试之间互不污染、跑完即删（tmp_path 由 pytest 自动清理），
而且完全不需要你本机装 MySQL。

从"加鉴权"这一轮开始，夹具分两种：
- client      已登录为 alice 的客户端（绝大多数接口需要登录）
- anon_client 未登录的客户端（测鉴权本身用）
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

from app.api.deps import get_session_factory_dep  # noqa: E402
from app.db.base import Base  # noqa: E402
from app.db.session import get_db  # noqa: E402
from app.main import create_app  # noqa: E402

_ALICE = {"username": "alice", "password": "pw12345678"}


def _build(tmp_path):
    """构造：临时数据库 + 覆盖依赖的 app + TestClient。"""
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
    # 后台任务也要用测试库：把会话工厂也换成测试用的
    app.dependency_overrides[get_session_factory_dep] = lambda: TestingSession

    # raise_server_exceptions=False：让未捕获异常也走统一错误处理器，返回 500 JSON
    return app, TestClient(app, raise_server_exceptions=False), engine


@pytest.fixture()
def client(tmp_path):
    """已登录为 alice 的客户端。"""
    app, test_client, engine = _build(tmp_path)
    with test_client:
        resp = test_client.post("/api/v1/auth/register", json=_ALICE)
        assert resp.status_code == 201, resp.text
        resp = test_client.post("/api/v1/auth/login", data=_ALICE)
        assert resp.status_code == 200, resp.text
        test_client.headers["Authorization"] = f"Bearer {resp.json()['access_token']}"
        yield test_client
    Base.metadata.drop_all(engine)
    engine.dispose()


@pytest.fixture()
def anon_client(tmp_path):
    """未登录的客户端（不注册任何用户）。"""
    app, test_client, engine = _build(tmp_path)
    with test_client:
        yield test_client
    Base.metadata.drop_all(engine)
    engine.dispose()


@pytest.fixture()
def login_as(client):
    """在 client 的同一个数据库里再注册/登录一个用户，返回其 token。

    用于"跨用户隔离"测试：拿到 bob 的 token 后，把 client 的
    Authorization 头换成 bob 的，验证他看不到/改不了 alice 的数据。
    """

    def _fn(username: str, password: str = "pw12345678") -> str:
        resp = client.post("/api/v1/auth/register", json={"username": username, "password": password})
        assert resp.status_code == 201, resp.text
        resp = client.post("/api/v1/auth/login", data={"username": username, "password": password})
        assert resp.status_code == 200, resp.text
        return resp.json()["access_token"]

    return _fn
