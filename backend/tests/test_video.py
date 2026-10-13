"""视频合成的测试：拼片、成片可播放性、前置条件、配额、鉴权、文件清理。

注意这类测试比其它测试慢（要真的跑 ffmpeg 编码），
所以用例尽量合并、只保留最有价值的断言。
"""

from app.api.deps import get_quota_service
from app.services.quota import QuotaService


def _prepare_full(client, *, with_images=True, with_audio=True):
    """建剧 -> 建集 -> 生成分镜 -> （可选）生成画面 -> （可选）生成配音。"""
    drama = client.post("/api/v1/dramas", json={"title": "深夜便利店", "genre": "悬疑"}).json()
    ep = client.post(
        f"/api/v1/dramas/{drama['id']}/episodes",
        json={"title": "第1集", "synopsis": "小林在夜班时发现异常。"},
    ).json()
    client.post(f"/api/v1/episodes/{ep['id']}/generate", json={"shot_count": 6})

    if with_images:
        task = client.post(f"/api/v1/episodes/{ep['id']}/images", json={}).json()
        assert client.get(f"/api/v1/tasks/{task['id']}").json()["status"] == "succeeded"

    if with_audio:
        task = client.post(f"/api/v1/episodes/{ep['id']}/audio", json={}).json()
        assert client.get(f"/api/v1/tasks/{task['id']}").json()["status"] == "succeeded"

    return drama, client.get(f"/api/v1/episodes/{ep['id']}").json()


def _compose(client, episode_id):
    resp = client.post(f"/api/v1/episodes/{episode_id}/video")
    assert resp.status_code == 202, resp.text
    task = resp.json()
    return client.get(f"/api/v1/tasks/{task['id']}").json()


def test_compose_video_end_to_end(client):
    """完整链路：分镜 -> 画面 -> 配音 -> 合成成片。"""
    _, episode = _prepare_full(client)

    finished = _compose(client, episode["id"])
    assert finished["status"] == "succeeded", finished.get("error")
    assert finished["kind"] == "video"
    assert finished["total"] == len(episode["shots"])
    assert finished["done"] == len(episode["shots"])

    detail = client.get(f"/api/v1/episodes/{episode['id']}").json()
    assert detail["video_path"], "应该生成了成片"
    assert detail["video_error"] is None

    resp = client.get(f"/api/v1/episodes/{episode['id']}/video")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "video/mp4"
    body = resp.content
    assert len(body) > 20_000, f"成片应该有实际内容，实际 {len(body)} 字节"
    # MP4 文件第 4-8 字节固定是 'ftyp'（box 类型），用它验证确实是 MP4
    assert body[4:8] == b"ftyp", "应该是一个真正的 MP4 文件"


def test_compose_video_requires_images(client):
    """没有画面就合成 -> 明确报错，而不是产出一个黑屏视频。"""
    _, episode = _prepare_full(client, with_images=False, with_audio=False)
    finished = _compose(client, episode["id"])

    assert finished["status"] == "failed"
    assert "没有画面" in finished["error"]

    detail = client.get(f"/api/v1/episodes/{episode['id']}").json()
    assert detail["video_path"] is None
    assert "没有画面" in detail["video_error"]


def test_compose_video_works_without_any_audio(client):
    """没有配音也能合成（用镜头自己的时长 + 静音音轨）。"""
    _, episode = _prepare_full(client, with_audio=False)
    finished = _compose(client, episode["id"])
    assert finished["status"] == "succeeded", finished.get("error")

    assert client.get(f"/api/v1/episodes/{episode['id']}/video").status_code == 200


def test_video_of_other_user_is_hidden(client, login_as):
    _, episode = _prepare_full(client)
    _compose(client, episode["id"])

    token = login_as("bob")
    client.headers["Authorization"] = f"Bearer {token}"
    assert client.get(f"/api/v1/episodes/{episode['id']}/video").status_code == 404
    assert client.post(f"/api/v1/episodes/{episode['id']}/video").status_code == 404


def test_video_without_composition_is_404(client):
    _, episode = _prepare_full(client, with_images=False, with_audio=False)
    resp = client.get(f"/api/v1/episodes/{episode['id']}/video")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "not_found"


def test_video_quota_is_separate(client):
    from app.api.deps import get_quota_service as dep

    client.app.dependency_overrides[dep] = lambda: QuotaService(limits={"shots": 5, "images": 5, "video": 1})
    _, episode = _prepare_full(client)

    body = client.post(f"/api/v1/episodes/{episode['id']}/video")
    assert body.status_code == 202
    resp = client.post(f"/api/v1/episodes/{episode['id']}/video")
    assert resp.status_code == 429
    assert "合成视频" in resp.json()["error"]["message"]


def test_deleting_drama_removes_video_file(client):
    import os

    drama, episode = _prepare_full(client)
    _compose(client, episode["id"])

    detail = client.get(f"/api/v1/episodes/{episode['id']}").json()
    video_file = os.path.join(os.environ["MEDIA_ROOT"], detail["video_path"])
    assert os.path.isfile(video_file)

    assert client.delete(f"/api/v1/dramas/{drama['id']}").status_code == 204
    assert not os.path.exists(video_file), "删除短剧后成片文件也应该被删掉"


def test_ffmpeg_is_available():
    """环境自检：没有 ffmpeg 的话所有视频功能都会失败，早点把问题暴露出来。"""
    import os

    from app.services.video_service import ffmpeg_exe

    path = ffmpeg_exe()
    assert path and os.path.isfile(path), f"ffmpeg 不可用: {path}"
