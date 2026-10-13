"""鉴权相关测试：注册、登录、令牌、资源隔离。"""


def _register(anon_client, username="bob", password="pw12345678"):
    return anon_client.post(
        "/api/v1/auth/register",
        json={"username": username, "password": password},
    )


def test_register_success_and_no_password_leak(anon_client):
    resp = _register(anon_client)
    assert resp.status_code == 201
    body = resp.json()
    assert body["username"] == "bob"
    # 关键断言：返回里绝不允许出现任何密码痕迹
    assert "password" not in body
    assert "password_hash" not in body


def test_register_duplicate_username(anon_client):
    assert _register(anon_client).status_code == 201
    resp = _register(anon_client)
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "conflict"


def test_register_validation(anon_client):
    # 用户名太短
    assert _register(anon_client, username="ab").status_code == 422
    # 密码太短
    assert anon_client.post(
        "/api/v1/auth/register", json={"username": "bob2", "password": "short"}
    ).status_code == 422
    # 用户名含非法字符
    assert anon_client.post(
        "/api/v1/auth/register", json={"username": "坏名字", "password": "pw12345678"}
    ).status_code == 422


def test_login_and_me_flow(anon_client):
    _register(anon_client)
    resp = anon_client.post("/api/v1/auth/login", data={"username": "bob", "password": "pw12345678"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["token_type"] == "bearer"
    assert body["access_token"]
    assert body["user"]["username"] == "bob"

    me = anon_client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {body['access_token']}"},
    )
    assert me.status_code == 200
    assert me.json()["username"] == "bob"


def test_login_wrong_password(anon_client):
    _register(anon_client)
    resp = anon_client.post("/api/v1/auth/login", data={"username": "bob", "password": "wrongpass"})
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "bad_credentials"


def test_login_unknown_user_also_401(anon_client):
    """不存在的用户和错误密码返回完全一样的 401/文案 —— 防用户名枚举。"""
    resp = anon_client.post("/api/v1/auth/login", data={"username": "ghost", "password": "whatever1"})
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "bad_credentials"


def test_me_without_token(anon_client):
    resp = anon_client.get("/api/v1/auth/me")
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "not_authenticated"


def test_me_with_garbage_token(anon_client):
    resp = anon_client.get("/api/v1/auth/me", headers={"Authorization": "Bearer not.a.real.token"})
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "invalid_token"


def test_protected_endpoints_require_login(anon_client):
    """没登录时，业务接口必须全部 401。"""
    assert anon_client.get("/api/v1/dramas").status_code == 401
    assert anon_client.post("/api/v1/dramas", json={"title": "x"}).status_code == 401
    assert anon_client.get("/api/v1/dramas/1").status_code == 401
    assert anon_client.post("/api/v1/episodes/1/generate", json={}).status_code == 401


def test_login_rate_limit_blocks_brute_force(anon_client):
    """连续错 3 次后，第 4 次（即使密码正确）也要被 429 拦下。

    这里通过依赖覆盖把限流窗口调成"最多 3 次"，否则测试要等 10 分钟。
    """
    from app.api.deps import get_login_limiter
    from app.core.ratelimit import LoginLimiter

    # 注意：必须复用同一个实例。如果写成 lambda: LoginLimiter(...)，
    # 每个请求都会拿到全新实例，失败计数永远攒不起来（这个坑在实现里也踩过）。
    limiter = LoginLimiter(max_attempts=3, window_sec=60)
    anon_client.app.dependency_overrides[get_login_limiter] = lambda: limiter
    _register(anon_client)

    for i in range(3):
        resp = anon_client.post("/api/v1/auth/login", data={"username": "bob", "password": "wrong-pass-1"})
        assert resp.status_code == 401, f"第 {i+1} 次失败登录应返回 401"

    # 第 4 次：已经被限流 —— 连"正确的密码"都进不来（这是限流的代价，也是设计取舍）
    resp = anon_client.post("/api/v1/auth/login", data={"username": "bob", "password": "pw12345678"})
    assert resp.status_code == 429
    assert resp.json()["error"]["code"] == "too_many_attempts"
