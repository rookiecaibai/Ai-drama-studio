"""配音功能的测试：生成、可播放性、只给有台词的镜头配音、配额、失败隔离、文件清理。"""

from app.api.deps import get_quota_service
from app.services.quota import QuotaService

WAV_MAGIC = b"RIFF"
WAVE_TAG = b"WAVE"


def _prepare_with_shots(client):
    drama = client.post("/api/v1/dramas", json={"title": "深夜便利店", "genre": "悬疑"}).json()
    ep = client.post(
        f"/api/v1/dramas/{drama['id']}/episodes",
        json={"title": "第1集", "synopsis": "小林在夜班时发现异常。"},
    ).json()
    client.post(f"/api/v1/episodes/{ep['id']}/generate", json={"shot_count": 6}).json()
    detail = client.get(f"/api/v1/episodes/{ep['id']}").json()
    assert detail["shots"]
    return drama, detail


def _run_audio_task(client, episode_id, only_missing=True):
    resp = client.post(f"/api/v1/episodes/{episode_id}/audio", json={"only_missing": only_missing})
    assert resp.status_code == 202, resp.text
    return resp.json()


def test_generate_audio_produces_playable_wav(client):
    _, episode = _prepare_with_shots(client)
    with_lines = [s for s in episode["shots"] if (s["dialogue"] or "").strip()]
    assert with_lines, "mock 分镜里应该有带台词的镜头"

    task = _run_audio_task(client, episode["id"])
    assert task["kind"] == "audio"

    finished = client.get(f"/api/v1/tasks/{task['id']}").json()
    assert finished["status"] == "succeeded"
    # 只有有台词的镜头才算进 total
    assert finished["total"] == len(with_lines)
    assert finished["done"] == len(with_lines)

    detail = client.get(f"/api/v1/episodes/{episode['id']}").json()
    for shot in detail["shots"]:
        has_line = bool((shot["dialogue"] or "").strip())
        if not has_line:
            assert shot["audio_path"] is None, "没有台词的镜头不该产生配音"
            continue

        assert shot["audio_path"], f"镜头 {shot['number']} 应该有配音了"
        assert shot["audio_error"] is None

        audio = client.get(f"/api/v1/shots/{shot['id']}/audio")
        assert audio.status_code == 200
        assert audio.headers["content-type"] == "audio/wav"
        assert audio.content[:4] == WAV_MAGIC
        assert audio.content[8:12] == WAVE_TAG
        assert len(audio.content) > 10_000, "应该是一段有长度的真实音频"


def test_audio_only_missing_skips_existing(client):
    _, episode = _prepare_with_shots(client)
    _run_audio_task(client, episode["id"])

    task = _run_audio_task(client, episode["id"], only_missing=True)
    finished = client.get(f"/api/v1/tasks/{task['id']}").json()
    assert finished["total"] == 0  # 全部已有配音

    task2 = _run_audio_task(client, episode["id"], only_missing=False)
    finished2 = client.get(f"/api/v1/tasks/{task2['id']}").json()
    assert finished2["total"] > 0  # 强制重做


def test_audio_of_other_user_is_hidden(client, login_as):
    _, episode = _prepare_with_shots(client)
    _run_audio_task(client, episode["id"])
    shot = next(s for s in episode["shots"] if (s["dialogue"] or "").strip())

    token = login_as("bob")
    client.headers["Authorization"] = f"Bearer {token}"
    assert client.get(f"/api/v1/shots/{shot['id']}/audio").status_code == 404
    assert client.post(f"/api/v1/episodes/{episode['id']}/audio", json={}).status_code == 404


def test_audio_without_generation_is_404(client):
    _, episode = _prepare_with_shots(client)
    shot = next(s for s in episode["shots"] if (s["dialogue"] or "").strip())
    resp = client.get(f"/api/v1/shots/{shot['id']}/audio")
    assert resp.status_code == 404


def test_audio_failure_is_isolated_per_shot(client):
    """某个镜头的台词全部合成失败，不影响其它镜头继续配音。

    注意现在有**两层**失败隔离：
      - 句级：一个镜头里某一句失败，其它句照样拼进镜头音频（见 test_dubbing.py）
      - 镜头级：一个镜头彻底失败，其它镜头继续（就是本用例）
    """
    from app.api.deps import get_tts_provider
    from app.services.tts.base import TtsProvider
    from app.services.tts.mock import MockTtsProvider

    class FlakyTts(TtsProvider):
        name = "flaky"
        extension = "wav"

        async def synthesize(self, *, text: str, style: str = "", voice=None, speed=None) -> bytes:
            # 让第一个镜头的每一句都失败（按台词内容精确命中，避免受调用顺序影响）
            if "那天之后" in text:
                raise RuntimeError("模拟配音服务超时")
            return await MockTtsProvider().synthesize(text=text, voice=voice, speed=speed)

    client.app.dependency_overrides[get_tts_provider] = lambda: FlakyTts()
    _, episode = _prepare_with_shots(client)

    task = _run_audio_task(client, episode["id"])
    finished = client.get(f"/api/v1/tasks/{task['id']}").json()
    assert finished["status"] == "succeeded"  # 部分成功仍算成功
    assert finished["done"] == finished["total"]

    detail = client.get(f"/api/v1/episodes/{episode['id']}").json()
    failed = [s for s in detail["shots"] if s["audio_error"]]
    ok = [s for s in detail["shots"] if s["audio_path"]]
    assert len(failed) == 1, "应该正好一个镜头失败"
    assert "超时" in failed[0]["audio_error"]
    assert len(ok) == finished["total"] - 1


def test_audio_quota_is_separate(client):
    from app.api.deps import get_quota_service as dep

    client.app.dependency_overrides[dep] = lambda: QuotaService(limits={"shots": 5, "images": 5, "audio": 1})
    _, episode = _prepare_with_shots(client)

    assert client.post(f"/api/v1/episodes/{episode['id']}/audio", json={}).status_code == 202
    resp = client.post(f"/api/v1/episodes/{episode['id']}/audio", json={})
    assert resp.status_code == 429
    assert "配音" in resp.json()["error"]["message"]


def test_audio_format_is_normalized_to_wav(client):
    """换厂商会换音频格式，但我们**统一转成 WAV 落盘**。

    为什么（这是刻意的设计，不是偷懒）：
    逐句音频要拼接、还要和画面一起封装进 MP4。中间文件格式统一，
    拼接和封装才是确定的（不用为每家厂商的 mp3/aac 分别适配）。
    所以"厂商产出什么格式"不再决定最终文件的扩展名 ——
    这条测试锁住的就是这个约定。
    """
    import os

    from app.api.deps import get_tts_provider
    from app.services.tts.base import TtsProvider

    class Mp3Tts(TtsProvider):
        name = "fake-mp3"
        extension = "mp3"

        async def synthesize(self, *, text, style="", voice=None, speed=None) -> bytes:
            # 故意返回"mp3"（其实是假字节）：最终落盘仍应是 wav
            return b"ID3" + b"\x00" * 2000

    _, episode = _prepare_with_shots(client)
    _run_audio_task(client, episode["id"])

    detail = client.get(f"/api/v1/episodes/{episode['id']}").json()
    shot = next(s for s in detail["shots"] if s["audio_path"])
    assert shot["audio_path"].endswith(".wav"), shot["audio_path"]

    # 逐句音频同样统一为 wav
    line_paths = [line["audio_path"] for s in detail["shots"] for line in s["lines"] if line["audio_path"]]
    assert line_paths, "应该有逐句音频"
    assert all(path.endswith(".wav") for path in line_paths)

    # 换厂商重做，路径稳定（同名覆盖），不该产生孤儿文件
    client.app.dependency_overrides[get_tts_provider] = lambda: Mp3Tts()
    _run_audio_task(client, episode["id"], only_missing=False)

    detail2 = client.get(f"/api/v1/episodes/{episode['id']}").json()
    shot2 = next(s for s in detail2["shots"] if s["id"] == shot["id"])
    assert shot2["audio_path"] == shot["audio_path"]

    media_root = os.environ["MEDIA_ROOT"]
    for path in line_paths:
        assert os.path.isfile(os.path.join(media_root, path))


def test_deleting_drama_removes_audio_files(client):
    import os

    drama, episode = _prepare_with_shots(client)
    _run_audio_task(client, episode["id"])

    detail = client.get(f"/api/v1/episodes/{episode['id']}").json()
    paths = [s["audio_path"] for s in detail["shots"] if s["audio_path"]]
    assert paths
    files = [os.path.join(os.environ["MEDIA_ROOT"], p) for p in paths]
    assert all(os.path.isfile(f) for f in files)

    assert client.delete(f"/api/v1/dramas/{drama['id']}").status_code == 204
    assert not any(os.path.exists(f) for f in files), "删除短剧后配音文件也应该被删掉"
