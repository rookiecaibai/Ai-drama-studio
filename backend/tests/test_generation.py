"""AI 生成任务流程的测试（异步版）。

关键点：
1. 测试里用的是 MockProvider，所以不联网、不花钱、结果稳定。
2. TestClient 会在返回响应前执行完 BackgroundTasks，
   所以创建任务后直接查状态就能看到终态（succeeded / failed），确定性可测。
"""


def _prepare_episode(client):
    drama = client.post(
        "/api/v1/dramas",
        json={"title": "雨夜便利店", "genre": "都市", "logline": "雨夜重逢", "style": "冷色调"},
    ).json()
    episode = client.post(f"/api/v1/dramas/{drama['id']}/episodes", json={"title": "第1集"}).json()
    return drama, episode


def _start_generation(client, episode_id, shot_count=6):
    """创建生成任务。返回任务 JSON（此刻应是 pending）。"""
    resp = client.post(f"/api/v1/episodes/{episode_id}/generate", json={"shot_count": shot_count})
    assert resp.status_code == 202, resp.text
    return resp.json()


def _wait_task(client, task_id):
    resp = client.get(f"/api/v1/tasks/{task_id}")
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_generate_creates_task_and_shots(client):
    _, episode = _prepare_episode(client)

    task = _start_generation(client, episode["id"], shot_count=6)
    # 刚返回时任务还没跑（响应先发、后台任务后执行）
    assert task["status"] == "pending"

    finished = _wait_task(client, task["id"])
    assert finished["status"] == "succeeded"
    assert finished["error"] is None
    assert finished["started_at"] is not None
    assert finished["finished_at"] is not None

    detail = client.get(f"/api/v1/episodes/{episode['id']}").json()
    assert detail["status"] == "ready"
    assert detail["title"] == "雨夜便利店"
    assert len(detail["shots"]) == 6
    assert [s["number"] for s in detail["shots"]] == [1, 2, 3, 4, 5, 6]


def test_regenerate_is_idempotent(client):
    _, episode = _prepare_episode(client)
    _start_generation(client, episode["id"])
    _start_generation(client, episode["id"])

    detail = client.get(f"/api/v1/episodes/{episode['id']}").json()
    assert len(detail["shots"]) == 6  # 两次生成仍然只有 6 个镜头


def test_task_list_filter_by_episode(client):
    _, episode = _prepare_episode(client)
    _start_generation(client, episode["id"])
    _start_generation(client, episode["id"])

    tasks = client.get(f"/api/v1/tasks?episode_id={episode['id']}").json()
    assert len(tasks) == 2
    assert all(t["episode_id"] == episode["id"] for t in tasks)
    assert all(t["status"] == "succeeded" for t in tasks)


def test_generate_unknown_episode_returns_404(client):
    resp = client.post("/api/v1/episodes/123456/generate", json={})
    assert resp.status_code == 404


def test_generate_rejects_bad_shot_count(client):
    _, episode = _prepare_episode(client)
    resp = client.post(f"/api/v1/episodes/{episode['id']}/generate", json={"shot_count": 999})
    assert resp.status_code == 422


def test_bad_llm_output_marks_task_failed(client):
    """模型返回垃圾内容时：任务标记 failed 并记录错误，而不是 500 崩栈。"""
    _, episode = _prepare_episode(client)

    from app.api.deps import get_llm_provider
    from app.services.llm.base import LLMProvider

    class BrokenProvider(LLMProvider):
        name = "broken"

        async def complete(self, *, system: str, user: str) -> str:
            return "抱歉，我无法完成这个请求。"

    client.app.dependency_overrides[get_llm_provider] = lambda: BrokenProvider()

    task = _start_generation(client, episode["id"])
    finished = _wait_task(client, task["id"])
    assert finished["status"] == "failed"
    assert finished["error"]  # 错误原因落库，方便排查

    # 剧集保持原样（draft、无镜头）
    detail = client.get(f"/api/v1/episodes/{episode['id']}").json()
    assert detail["status"] == "draft"
    assert detail["shots"] == []


def test_task_visibility_is_per_user(client, login_as):
    _, episode = _prepare_episode(client)
    task = _start_generation(client, episode["id"])

    bob_token = login_as("bob")
    client.headers["Authorization"] = f"Bearer {bob_token}"
    assert client.get(f"/api/v1/tasks/{task['id']}").status_code == 404
