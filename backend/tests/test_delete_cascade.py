"""删除剧集 / 删除角色的测试。

重点不是"记录删掉了"（数据库级联会做），而是**磁盘文件有没有一起清掉** ——
这是最容易被忽略、删不干净就会一直堆着的地方（视频文件尤其大）。
另外守护两个用户能感知到的后果：
    ① 删某一集，**其它集的编号不能变**（不然对不上之前聊过的"第 3 集"）；
    ② 删角色后，**台词还在**、说话人的文字名字还在，只是不再有音色。
"""

from sqlalchemy import select

from app.api.deps import get_session_factory_dep
from app.db.models import Character, DialogueLine, Shot
from app.services import media

# 一个最小合法 PNG 头 —— 让 media.detect_image_extension 按文件头认格式
FAKE_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 128


def _factory(client):
    """测试库的会话工厂（conftest 把依赖覆盖成了临时库）。

    注意这里要调用一次覆盖值：`dependency_overrides[...]` 存的是一个**返回工厂的函数**，
    拿到工厂之后再 `factory()` 才是 Session。
    """
    return client.app.dependency_overrides[get_session_factory_dep]()


def _make_drama(client, title: str = "删除测试剧") -> dict:
    resp = client.post("/api/v1/dramas", json={"title": title, "genre": "悬疑"})
    assert resp.status_code == 201, resp.text
    return resp.json()


def _make_episode(client, drama_id: int, title: str) -> dict:
    resp = client.post(f"/api/v1/dramas/{drama_id}/episodes", json={"title": title})
    assert resp.status_code == 201, resp.text
    return resp.json()


def _make_shot_with_media(factory, episode_id: int, number: int, *, with_clip: bool = False) -> dict:
    """直接插一条分镜，并真的在磁盘上放好图片 / 音频（可选片段）。"""
    image_rel = f"shots/test_{episode_id}_{number}.png"
    audio_rel = f"audio/test_{episode_id}_{number}.mp3"
    line_audio_rel = f"audio/test_line_{episode_id}_{number}.mp3"
    clip_rel = f"clips/test_{episode_id}_{number}.mp4"

    media.save_bytes(image_rel, FAKE_PNG)
    media.save_bytes(audio_rel, b"ID3" + b"\x00" * 32)
    media.save_bytes(line_audio_rel, b"ID3" + b"\x00" * 16)
    if with_clip:
        media.save_bytes(clip_rel, b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 32)

    with factory() as db:
        shot = Shot(
            episode_id=episode_id,
            number=number,
            description="测试镜头",
            dialogue="测试角色：这是一句台词。",
            image_path=image_rel,
            audio_path=audio_rel,
            clip_path=clip_rel if with_clip else None,
        )
        db.add(shot)
        db.flush()
        db.add(
            DialogueLine(
                shot_id=shot.id,
                index=0,
                speaker="测试角色",
                text="这是一句台词。",
                audio_path=line_audio_rel,
            )
        )
        db.commit()
        shot_id = shot.id

    return {
        "shot_id": shot_id,
        "image": image_rel,
        "audio": audio_rel,
        "line_audio": line_audio_rel,
        "clip": clip_rel if with_clip else None,
    }


def test_delete_episode_removes_media_and_keeps_other_numbers(client):
    drama = _make_drama(client)
    first = _make_episode(client, drama["id"], "第一集")
    second = _make_episode(client, drama["id"], "第二集")

    factory = _factory(client)
    files = _make_shot_with_media(factory, second["id"], 1, with_clip=True)
    for key in ("image", "audio", "line_audio", "clip"):
        assert media.exists(files[key]), f"前置条件：{files[key]} 应该已写盘"

    resp = client.delete(f"/api/v1/episodes/{second['id']}")
    assert resp.status_code == 204, resp.text

    # 记录没了
    assert client.get(f"/api/v1/episodes/{second['id']}").status_code == 404
    # 磁盘文件也清掉了 —— 这是这个测试的重点
    for key in ("image", "audio", "line_audio", "clip"):
        assert not media.exists(files[key]), f"{files[key]} 应该被删掉"

    # 其它集还在，**编号没变**
    remaining = client.get(f"/api/v1/dramas/{drama['id']}/episodes").json()
    assert [e["number"] for e in remaining] == [first["number"]]
    assert remaining[0]["id"] == first["id"]


def test_delete_episode_of_another_user_is_404(client, login_as):
    drama = _make_drama(client)
    episode = _make_episode(client, drama["id"], "别人不能删")
    bob = login_as("bob")
    resp = client.delete(
        f"/api/v1/episodes/{episode['id']}", headers={"Authorization": f"Bearer {bob}"}
    )
    assert resp.status_code == 404, resp.text
    # 没被删掉
    assert client.get(f"/api/v1/episodes/{episode['id']}").status_code == 200


def test_delete_character_keeps_dialogue_but_drops_link(client):
    drama = _make_drama(client)
    episode = _make_episode(client, drama["id"], "第一集")
    character = client.post(
        f"/api/v1/dramas/{drama['id']}/characters",
        json={"name": "王警官", "gender": "male", "age": 52},
    ).json()

    factory = _factory(client)
    files = _make_shot_with_media(factory, episode["id"], 1)

    # 模拟这个角色已经画过三视图
    sheet_rel = media.character_sheet_rel_path(character["id"], "png")
    media.save_bytes(sheet_rel, FAKE_PNG)
    portrait_rel = media.character_portrait_rel_path(character["id"])
    media.save_bytes(portrait_rel, FAKE_PNG)

    with factory() as db:
        row = db.get(Character, character["id"])
        row.turnaround_path = sheet_rel
        line = db.scalar(select(DialogueLine).where(DialogueLine.shot_id == files["shot_id"]))
        line.character_id = character["id"]
        db.commit()

    resp = client.delete(f"/api/v1/characters/{character['id']}")
    assert resp.status_code == 204, resp.text

    # 角色没了，三视图和参考图都清了
    assert client.get(f"/api/v1/dramas/{drama['id']}/characters").json() == []
    assert not media.exists(sheet_rel)
    assert not media.exists(portrait_rel)

    # **台词还在**，说话人的文字名字还在，只是不再挂角色
    with factory() as db:
        line = db.scalar(select(DialogueLine).where(DialogueLine.shot_id == files["shot_id"]))
        assert line is not None, "删角色不该删掉台词"
        assert line.speaker == "测试角色"
        assert line.character_id is None, "关联应该被数据库置空（ON DELETE SET NULL）"


def test_delete_character_of_another_user_is_404(client, login_as):
    drama = _make_drama(client)
    character = client.post(
        f"/api/v1/dramas/{drama['id']}/characters", json={"name": "李四"}
    ).json()
    bob = login_as("bob")
    resp = client.delete(
        f"/api/v1/characters/{character['id']}", headers={"Authorization": f"Bearer {bob}"}
    )
    assert resp.status_code == 404, resp.text
    assert len(client.get(f"/api/v1/dramas/{drama['id']}/characters").json()) == 1
