"""文生图功能测试：生成、存盘、带鉴权读取、配额、失败隔离。"""

from app.api.deps import get_quota_service
from app.services.quota import QuotaService

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def _prepare_with_shots(client):
    """建一部剧、一集、并生成 3 个镜头（走 mock 文本模型）。"""
    drama = client.post("/api/v1/dramas", json={"title": "地铁末班车", "genre": "悬疑"}).json()
    ep = client.post(
        f"/api/v1/dramas/{drama['id']}/episodes",
        json={"title": "第1集", "synopsis": "男主在末班车上察觉异常。"},
    ).json()
    task = client.post(f"/api/v1/episodes/{ep['id']}/generate", json={"shot_count": 3}).json()
    detail = client.get(f"/api/v1/episodes/{ep['id']}").json()
    assert detail["shots"], "分镜应该已经生成"
    return drama, detail, task


def _run_image_task(client, episode_id, only_missing=True):
    resp = client.post(f"/api/v1/episodes/{episode_id}/images", json={"only_missing": only_missing})
    assert resp.status_code == 202, resp.text
    return resp.json()


def test_generate_images_creates_files_and_serves_them(client):
    _, episode, _ = _prepare_with_shots(client)

    task = _run_image_task(client, episode["id"])
    assert task["kind"] == "images"
    assert task["status"] == "pending"

    finished = client.get(f"/api/v1/tasks/{task['id']}").json()
    assert finished["status"] == "succeeded"
    assert finished["total"] == len(episode["shots"])
    assert finished["done"] == len(episode["shots"])

    detail = client.get(f"/api/v1/episodes/{episode['id']}").json()
    for shot in detail["shots"]:
        assert shot["image_path"], f"镜头 {shot['number']} 应该有画面了"
        assert shot["image_error"] is None

        img = client.get(f"/api/v1/shots/{shot['id']}/image")
        assert img.status_code == 200
        assert img.headers["content-type"] == "image/png"
        assert img.content.startswith(PNG_MAGIC), "应该是一张真实的 PNG"
        assert len(img.content) > 1000


def test_image_is_idempotent_and_only_missing_skips(client):
    _, episode, _ = _prepare_with_shots(client)
    _run_image_task(client, episode["id"])

    # 再跑一次 only_missing=True：没有要处理的镜头
    task = _run_image_task(client, episode["id"], only_missing=True)
    finished = client.get(f"/api/v1/tasks/{task['id']}").json()
    assert finished["status"] == "succeeded"
    assert finished["total"] == 0  # 全部已有画面，跳过
    assert finished["done"] == 0

    # only_missing=False：全部重画
    task2 = _run_image_task(client, episode["id"], only_missing=False)
    finished2 = client.get(f"/api/v1/tasks/{task2['id']}").json()
    assert finished2["total"] == len(episode["shots"])


def test_image_of_other_user_is_hidden(client, login_as):
    _, episode, _ = _prepare_with_shots(client)
    _run_image_task(client, episode["id"])
    shot_id = episode["shots"][0]["id"]

    token = login_as("bob")
    client.headers["Authorization"] = f"Bearer {token}"
    assert client.get(f"/api/v1/shots/{shot_id}/image").status_code == 404
    assert client.post(f"/api/v1/episodes/{episode['id']}/images", json={}).status_code == 404


def test_image_without_generation_is_404(client):
    _, episode, _ = _prepare_with_shots(client)
    shot_id = episode["shots"][0]["id"]
    resp = client.get(f"/api/v1/shots/{shot_id}/image")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "not_found"


def test_image_generation_failure_is_isolated_per_shot(client):
    """单张失败不影响其它镜头，失败原因记在镜头自己身上。"""
    from app.api.deps import get_image_provider
    from app.services.images.base import ImageProvider

    class FlakyProvider(ImageProvider):
        name = "flaky"

        def __init__(self):
            self.calls = 0

        async def generate(self, *, prompt: str) -> bytes:
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("模拟第一张图生成超时")
            return b"\x89PNG\r\n\x1a\n" + b"fake-png-body" * 100

    flaky = FlakyProvider()
    client.app.dependency_overrides[get_image_provider] = lambda: flaky
    _, episode, _ = _prepare_with_shots(client)

    task = _run_image_task(client, episode["id"])
    finished = client.get(f"/api/v1/tasks/{task['id']}").json()
    assert finished["status"] == "succeeded"  # 部分成功仍算成功
    assert finished["done"] == finished["total"]

    detail = client.get(f"/api/v1/episodes/{episode['id']}").json()
    failed = [s for s in detail["shots"] if s["image_error"]]
    ok = [s for s in detail["shots"] if s["image_path"]]
    assert len(failed) == 1, "应该正好一张失败"
    assert "超时" in failed[0]["image_error"]
    assert len(ok) == finished["total"] - 1


def test_all_images_failed_marks_task_failed(client):
    from app.api.deps import get_image_provider
    from app.services.images.base import ImageProvider

    class DeadProvider(ImageProvider):
        name = "dead"

        async def generate(self, *, prompt: str) -> bytes:
            raise RuntimeError("模型服务不可用")

    client.app.dependency_overrides[get_image_provider] = lambda: DeadProvider()
    _, episode, _ = _prepare_with_shots(client)

    task = _run_image_task(client, episode["id"])
    finished = client.get(f"/api/v1/tasks/{task['id']}").json()
    assert finished["status"] == "failed"
    assert "全部生成失败" in finished["error"]


def test_image_quota_is_separate_from_text_quota(client):
    """图片配额和文本配额是分开的：文本用完不影响图片。"""
    from app.api.deps import get_quota_service as dep

    client.app.dependency_overrides[dep] = lambda: QuotaService(limits={"shots": 1, "synopsis": 1, "images": 1})
    _, episode, _ = _prepare_with_shots(client)

    # images 额度只有 1：第一次成功，第二次 429
    assert client.post(f"/api/v1/episodes/{episode['id']}/images", json={}).status_code == 202
    resp = client.post(f"/api/v1/episodes/{episode['id']}/images", json={})
    assert resp.status_code == 429
    assert "生成画面" in resp.json()["error"]["message"]

    # 但 shots 的额度是独立计的（分镜已经生成过了，这里只验证错误信息里提到的是"画面"）


def test_image_generation_requires_shots_first(client):
    """还没有分镜就想生成画面 -> 任务失败并说明原因。"""
    drama = client.post("/api/v1/dramas", json={"title": "空剧", "genre": "都市"}).json()
    ep = client.post(f"/api/v1/dramas/{drama['id']}/episodes", json={"title": "第1集"}).json()

    task = _run_image_task(client, ep["id"])
    finished = client.get(f"/api/v1/tasks/{task['id']}").json()
    assert finished["status"] == "failed"
    assert "先生成分镜" in finished["error"]


def test_deleting_drama_removes_image_files(client):
    """删短剧要顺手删磁盘上的图片 —— 数据库级联删除不会管文件。"""
    import os

    drama, episode, _ = _prepare_with_shots(client)
    _run_image_task(client, episode["id"])

    detail = client.get(f"/api/v1/episodes/{episode['id']}").json()
    paths = [s["image_path"] for s in detail["shots"]]
    assert paths and all(paths)

    media_root = os.environ["MEDIA_ROOT"]
    files = [os.path.join(media_root, p) for p in paths]
    assert all(os.path.isfile(f) for f in files), "生成后文件应该存在"

    assert client.delete(f"/api/v1/dramas/{drama['id']}").status_code == 204

    assert not any(os.path.exists(f) for f in files), "删除短剧后图片文件也应该被删掉"
