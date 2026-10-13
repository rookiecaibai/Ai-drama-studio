"""大纲相关功能测试：用户自己写大纲、AI 起草大纲、以及"用户输入优先于模型建议"。"""

from app.api.deps import get_quota_service
from app.services.quota import QuotaService


def _prepare(client, synopsis=""):
    drama = client.post(
        "/api/v1/dramas",
        json={"title": "地铁末班车", "genre": "悬疑", "logline": "末班地铁上的乘客长着同一张脸", "style": "冷蓝调"},
    ).json()
    ep = client.post(f"/api/v1/dramas/{drama['id']}/episodes", json={"title": "第1集", "synopsis": synopsis}).json()
    return drama, ep


def test_user_can_write_and_edit_synopsis(client):
    """用户自己写的大纲能存下来、能改 —— 这是本次功能的核心。"""
    _, ep = _prepare(client, synopsis="我写的大纲：男主上车，发现异常。")
    assert ep["synopsis"] == "我写的大纲：男主上车，发现异常。"

    resp = client.patch(f"/api/v1/episodes/{ep['id']}", json={"synopsis": "改过的大纲", "title": "新标题"})
    assert resp.status_code == 200
    assert resp.json()["synopsis"] == "改过的大纲"
    assert resp.json()["title"] == "新标题"

    # 只传 synopsis 时，title 不应被清空（PATCH 语义）
    resp = client.patch(f"/api/v1/episodes/{ep['id']}", json={"synopsis": "再改一次"})
    assert resp.json()["title"] == "新标题"
    assert resp.json()["synopsis"] == "再改一次"


def test_patch_episode_of_other_user_is_404(client, login_as):
    _, ep = _prepare(client)
    token = login_as("bob")
    client.headers["Authorization"] = f"Bearer {token}"
    assert client.patch(f"/api/v1/episodes/{ep['id']}", json={"synopsis": "偷改"}).status_code == 404


def test_ai_generate_synopsis_does_not_overwrite(client):
    """AI 起草大纲只返回建议，绝不写库 —— 用户必须先确认。"""
    drama, ep = _prepare(client, synopsis="用户原本写的大纲")

    # after_number=1 表示"给第 1 集起草大纲"，前情是编号 < 1 的剧集 -> 空
    resp = client.post(
        f"/api/v1/dramas/{drama['id']}/synopsis/generate",
        json={"title": "第1集", "after_number": 1},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["synopsis"]  # 有内容返回
    assert body["used_context_episodes"] == []  # 第一集没有前情

    # 关键断言：库里的大纲必须还是用户写的那个（AI 不能覆盖用户的内容）
    detail = client.get(f"/api/v1/episodes/{ep['id']}").json()
    assert detail["synopsis"] == "用户原本写的大纲"


def test_ai_synopsis_uses_previous_episodes_as_context(client):
    """生成第 3 集大纲时，应该把第 1、2 集作为前情带进去。"""
    drama = client.post("/api/v1/dramas", json={"title": "地铁末班车", "genre": "悬疑"}).json()
    for n in (1, 2):
        client.post(
            f"/api/v1/dramas/{drama['id']}/episodes",
            json={"number": n, "title": f"第{n}集", "synopsis": f"第{n}集发生的事"},
        )

    # 新建第 3 集之前起草大纲：after_number 不传 = 接在现有剧集之后
    resp = client.post(f"/api/v1/dramas/{drama['id']}/synopsis/generate", json={"title": "第3集"})
    assert resp.status_code == 200
    assert resp.json()["used_context_episodes"] == [1, 2]

    # 给已存在的第 2 集起草大纲：上下文只应包含第 1 集
    resp = client.post(
        f"/api/v1/dramas/{drama['id']}/synopsis/generate",
        json={"title": "第2集", "after_number": 2},
    )
    assert resp.json()["used_context_episodes"] == [1]


def test_ai_synopsis_requires_auth_and_ownership(anon_client, client, login_as):
    drama, _ = _prepare(client)

    # 未登录
    assert anon_client.post(f"/api/v1/dramas/{drama['id']}/synopsis/generate", json={}).status_code == 401

    # 别人的剧 -> 404
    token = login_as("bob")
    client.headers["Authorization"] = f"Bearer {token}"
    assert client.post(f"/api/v1/dramas/{drama['id']}/synopsis/generate", json={}).status_code == 404


def test_ai_synopsis_consumes_quota(client):
    """生成大纲也是真花钱的调用，所以必须和生成分镜共用配额。"""
    client.app.dependency_overrides[get_quota_service] = lambda: QuotaService(daily_limit=1)
    drama, _ = _prepare(client)

    assert client.post(f"/api/v1/dramas/{drama['id']}/synopsis/generate", json={"title": "第1集"}).status_code == 200
    resp = client.post(f"/api/v1/dramas/{drama['id']}/synopsis/generate", json={"title": "第2集"})
    assert resp.status_code == 429
    assert resp.json()["error"]["code"] == "quota_exceeded"


def test_generation_keeps_user_synopsis(client):
    """生成分镜时，用户写好的大纲不能被模型返回的梗概覆盖。"""
    drama, ep = _prepare(client, synopsis="用户精心写的大纲，不能被覆盖")

    task = client.post(f"/api/v1/episodes/{ep['id']}/generate", json={"shot_count": 4}).json()
    assert task["status"] == "pending"
    finished = client.get(f"/api/v1/tasks/{task['id']}").json()
    assert finished["status"] == "succeeded"

    detail = client.get(f"/api/v1/episodes/{ep['id']}").json()
    assert detail["synopsis"] == "用户精心写的大纲，不能被覆盖"  # 用户的内容优先
    # 镜头照常生成。注意：MockProvider 固定返回 6 个镜头、不看 shot_count，
    # 所以这里断言 6（真实大模型才会遵守我们传的 4）。
    assert len(detail["shots"]) == 6
