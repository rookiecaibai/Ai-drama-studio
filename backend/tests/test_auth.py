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
