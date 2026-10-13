"""配额测试：每日生成上限 + 失败也消耗额度。"""

from app.api.deps import get_quota_service
from app.services.quota import QuotaService


def _prepare(client):
    drama = client.post(
        "/api/v1/dramas",
        json={"title": "雨夜便利店", "genre": "都市", "logline": "雨夜重逢", "style": "冷色调"},
    ).json()
    episode = client.post(f"/api/v1/dramas/{drama['id']}/episodes", json={"title": "第1集"}).json()
    return episode


def test_quota_blocks_after_limit(client):
    """上限设为 2：前两次成功，第三次被 429 拦下。"""
    client.app.dependency_overrides[get_quota_service] = lambda: QuotaService(daily_limit=2)
    episode = _prepare(client)

    for i in range(2):
        resp = client.post(f"/api/v1/episodes/{episode['id']}/generate", json={"shot_count": 6})
        assert resp.status_code == 200, f"第 {i+1} 次应成功: {resp.text}"

    resp = client.post(f"/api/v1/episodes/{episode['id']}/generate", json={"shot_count": 6})
    assert resp.status_code == 429
    assert resp.json()["error"]["code"] == "quota_exceeded"


def test_failed_generation_also_consumes_quota(client):
    """失败的大模型调用一样要付钱，所以必须消耗额度。

    注入一个坏模型让生成必然失败（502），然后验证额度被占掉：
    第二次调用直接 429，而不是又能试一次。
    """
    from app.api.deps import get_llm_provider
    from app.services.llm.base import LLMProvider

    class BrokenProvider(LLMProvider):
        name = "broken"

        async def complete(self, *, system: str, user: str) -> str:
            return "抱歉，我无法完成这个请求。"

    client.app.dependency_overrides[get_llm_provider] = lambda: BrokenProvider()
    client.app.dependency_overrides[get_quota_service] = lambda: QuotaService(daily_limit=1)
    episode = _prepare(client)

    first = client.post(f"/api/v1/episodes/{episode['id']}/generate", json={"shot_count": 6})
    assert first.status_code == 502  # 第一次：失败，但流水已记为 failed

    second = client.post(f"/api/v1/episodes/{episode['id']}/generate", json={"shot_count": 6})
    assert second.status_code == 429  # 第二次：额度已被失败的调用占掉
    assert second.json()["error"]["code"] == "quota_exceeded"


def test_quota_is_per_user(client, login_as):
    """配额按用户独立计算：bob 用完自己的额度不影响 alice。"""
    client.app.dependency_overrides[get_quota_service] = lambda: QuotaService(daily_limit=1)
    episode = _prepare(client)  # alice 的剧

    # alice 用掉她自己的 1 次额度
    assert client.post(f"/api/v1/episodes/{episode['id']}/generate", json={"shot_count": 6}).status_code == 200

    # bob 登录后新建自己的剧，他还有自己的额度（新用户）
    bob_token = login_as("bob")
    client.headers["Authorization"] = f"Bearer {bob_token}"
    bob_episode = _prepare(client)
    assert client.post(f"/api/v1/episodes/{bob_episode['id']}/generate", json={"shot_count": 6}).status_code == 200

    # 但 alice 已经用完了，切回 alice 应该 429
    resp = client.post("/api/v1/auth/login", data={"username": "alice", "password": "pw12345678"})
    client.headers["Authorization"] = f"Bearer {resp.json()['access_token']}"
    assert client.post(f"/api/v1/episodes/{episode['id']}/generate", json={"shot_count": 6}).status_code == 429
