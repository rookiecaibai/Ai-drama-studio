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
