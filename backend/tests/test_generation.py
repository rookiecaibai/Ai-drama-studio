"""AI 生成流程的测试。

关键点：测试里用的是 MockProvider，所以**不联网、不花钱、结果稳定**。
面试时这也是个加分话术："我把大模型调用抽象成接口，测试用假实现，
所以 CI 不依赖外部 API，也不会因为模型抽风而随机挂。"
"""


def _prepare_episode(client):
    drama = client.post(
        "/api/v1/dramas",
        json={"title": "雨夜便利店", "genre": "都市", "logline": "雨夜重逢", "style": "冷色调"},
    ).json()
    episode = client.post(f"/api/v1/dramas/{drama['id']}/episodes", json={"title": "第1集"}).json()
    return drama, episode


def test_generate_creates_shots(client):
    _, episode = _prepare_episode(client)

    resp = client.post(f"/api/v1/episodes/{episode['id']}/generate", json={"shot_count": 6})
    assert resp.status_code == 200, resp.text

    body = resp.json()
    assert body["status"] == "ready"
    assert body["title"] == "雨夜便利店"  # 被 mock 结果覆盖
    assert len(body["shots"]) == 6

    numbers = [s["number"] for s in body["shots"]]
    assert numbers == [1, 2, 3, 4, 5, 6]
    assert all(s["description"] for s in body["shots"])
    assert body["shots"][0]["shot_type"] == "特写"


def test_generate_is_idempotent(client):
    """重复生成不会让镜头翻倍 —— 这是"全量替换"策略要保证的。"""
    _, episode = _prepare_episode(client)

    client.post(f"/api/v1/episodes/{episode['id']}/generate", json={"shot_count": 6})
    second = client.post(f"/api/v1/episodes/{episode['id']}/generate", json={"shot_count": 6})

    assert second.status_code == 200
    assert len(second.json()["shots"]) == 6

    detail = client.get(f"/api/v1/episodes/{episode['id']}")
    assert len(detail.json()["shots"]) == 6


def test_generate_unknown_episode_returns_404(client):
    resp = client.post("/api/v1/episodes/123456/generate", json={})
    assert resp.status_code == 404


def test_generate_rejects_bad_shot_count(client):
    _, episode = _prepare_episode(client)
    resp = client.post(f"/api/v1/episodes/{episode['id']}/generate", json={"shot_count": 999})
    assert resp.status_code == 422


def test_llm_bad_output_returns_502(client):
    """模型返回垃圾内容时，应返回明确的 502 而不是 500 崩栈。

    这里通过依赖覆盖注入一个"坏模型"，验证错误处理路径。
    """
    _, episode = _prepare_episode(client)

    from app.api.deps import get_llm_provider
    from app.services.llm.base import LLMProvider

    class BrokenProvider(LLMProvider):
        name = "broken"

        async def complete(self, *, system: str, user: str) -> str:
            return "抱歉，我无法完成这个请求。"

    client.app.dependency_overrides[get_llm_provider] = lambda: BrokenProvider()
    resp = client.post(f"/api/v1/episodes/{episode['id']}/generate", json={})

    assert resp.status_code == 502
    assert resp.json()["error"]["code"] == "llm_bad_output"
