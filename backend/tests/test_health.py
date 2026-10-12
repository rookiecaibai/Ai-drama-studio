def test_health_ok(client):
    resp = client.get("/api/v1/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["llm_provider"] == "mock"


def test_openapi_available(client):
    """接口文档能被生成，说明所有 response_model 都是合法的。"""
    resp = client.get("/openapi.json")
    assert resp.status_code == 200
    paths = resp.json()["paths"]
    assert "/api/v1/dramas" in paths
    assert "/api/v1/episodes/{episode_id}/generate" in paths
