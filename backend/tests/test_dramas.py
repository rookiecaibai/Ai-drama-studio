def _create_drama(client, title="雨夜便利店"):
    resp = client.post(
        "/api/v1/dramas",
        json={"title": title, "genre": "都市", "logline": "雨夜的一次重逢", "style": "冷色调、手持"},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_drama_crud_flow(client):
    drama = _create_drama(client)
    assert drama["id"] > 0
    assert drama["status"] == "draft"

    # 列表
    lst = client.get("/api/v1/dramas")
    assert lst.status_code == 200
    assert len(lst.json()) == 1

    # 局部更新：只传 title，genre 应保持原值
    patched = client.patch(f"/api/v1/dramas/{drama['id']}", json={"title": "雨夜便利店 II"})
    assert patched.status_code == 200
    assert patched.json()["title"] == "雨夜便利店 II"
    assert patched.json()["genre"] == "都市"

    # 删除后应 404
    deleted = client.delete(f"/api/v1/dramas/{drama['id']}")
    assert deleted.status_code == 204
    assert client.get(f"/api/v1/dramas/{drama['id']}").status_code == 404


def test_create_drama_validation_error(client):
    """空标题必须被拦住 —— 这是 Pydantic 在做参数校验，不用手写 if。"""
    resp = client.post("/api/v1/dramas", json={"title": ""})
    assert resp.status_code == 422


def test_not_found_uses_unified_error_shape(client):
    resp = client.get("/api/v1/dramas/99999")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "not_found"


def test_episode_number_auto_increment_and_duplicate(client):
    drama = _create_drama(client)

    first = client.post(f"/api/v1/dramas/{drama['id']}/episodes", json={"title": "第1集"})
    assert first.status_code == 201
    assert first.json()["number"] == 1

    second = client.post(f"/api/v1/dramas/{drama['id']}/episodes", json={"title": "第2集"})
    assert second.json()["number"] == 2

    # 指定重复集数应 409
    dup = client.post(f"/api/v1/dramas/{drama['id']}/episodes", json={"number": 1})
    assert dup.status_code == 409
    assert dup.json()["error"]["code"] == "conflict"

    lst = client.get(f"/api/v1/dramas/{drama['id']}/episodes")
    assert [e["number"] for e in lst.json()] == [1, 2]


def test_cross_user_isolation(client, login_as):
    """核心安全用例：用户 B 不能看到、修改或删除用户 A 的资源。"""
    drama = _create_drama(client)  # alice 的剧

    bob_token = login_as("bob")
    client.headers["Authorization"] = f"Bearer {bob_token}"

    # B 看 A 的剧：404（不是 403 —— 403 会泄露"这个 id 存在"）
    assert client.get(f"/api/v1/dramas/{drama['id']}").status_code == 404
    # B 改 A 的剧：404
    assert client.patch(f"/api/v1/dramas/{drama['id']}", json={"title": "偷改"}).status_code == 404
    # B 删 A 的剧：404，且剧依然存在
    assert client.delete(f"/api/v1/dramas/{drama['id']}").status_code == 404
    # B 给 A 的剧加集：404
    assert client.post(f"/api/v1/dramas/{drama['id']}/episodes", json={"title": "偷加"}).status_code == 404
    # B 的列表里看不到 A 的剧
    ids = [d["id"] for d in client.get("/api/v1/dramas").json()]
    assert drama["id"] not in ids

    # 切回原来的 alice（只是重新登录，不是注册新用户），她的剧还在
    resp = client.post("/api/v1/auth/login", data={"username": "alice", "password": "pw12345678"})
    client.headers["Authorization"] = f"Bearer {resp.json()['access_token']}"
    assert client.get(f"/api/v1/dramas/{drama['id']}").status_code == 200

