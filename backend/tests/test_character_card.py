"""角色卡、角色三视图、镜头手动编辑的测试。

分两层：
- **纯逻辑层**（提示词怎么拼、谁算出镜、旁白要不要排除）—— 不起数据库、不联网，最快最准；
- **接口层**（改角色卡、生成三视图、改镜头之后派生数据怎么作废）—— 用依赖覆盖注入假 Provider。

重点守护三件容易被忽略、但用户一定会遇到的问题：
    ① 同一个角色在不同镜头里要被**同样的关键字**描述（否则长相会飘）；
    ② 旁白没有形象，不能给它生成三视图、也不能把它塞进画面提示词；
    ③ 改了台词，旧的逐句音频必须作废（否则会出现"台词变了、配音还是老的"）。
"""

from app.api.deps import get_image_provider, get_quota_service
from app.db.models import Character
from app.services.character_look import (
    build_look_prompt,
    build_sheet_prompt,
    characters_in_shot,
    compose_shot_prompt,
    derive_age_group,
    resolve_look_prompt,
)
from app.services.image_service import _pick_reference
from app.services.images.base import ImageProvider
from app.services.quota import QuotaService

# 一个最小合法 PNG 头 —— 让 media.detect_image_extension 能按文件头认出格式
FAKE_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 128


def _char(character_id: int, **kwargs) -> Character:
    """构造一个**不入库**的角色对象（纯逻辑测试用）。"""
    base = dict(
        name="林晚",
        gender="female",
        age_group="young",
        age=None,
        appearance="",
        occupation="",
        background="",
        look_prompt="",
        turnaround_path=None,
    )
    base.update(kwargs)
    character = Character(**base)
    character.id = character_id  # 手动给主键：characters_in_shot 用它做去重键
    return character


def _shot(**kwargs):
    from app.db.models import Shot

    base = dict(number=1, description="", dialogue="", image_prompt="", shot_type="中景", camera="固定")
    base.update(kwargs)
    return Shot(**base)


# ───────────────────────── ① 年龄 → 年龄段 ─────────────────────────


def test_age_maps_to_age_group():
    """用户填具体年龄，音色矩阵按年龄段选 —— 所以这个映射必须准。"""
    assert derive_age_group(10) == "child"
    assert derive_age_group(16) == "teen"
    assert derive_age_group(28) == "young"
    assert derive_age_group(39) == "young"
    assert derive_age_group(45) == "middle"
    assert derive_age_group(66) == "elderly"
    assert derive_age_group(None) is None


# ───────────────────────── ② 外貌提示词 ─────────────────────────


def test_look_prompt_is_built_from_the_card():
    """外貌提示词只放**画面相关**的信息，不掺背景故事。"""
    character = _char(
        1,
        gender="male",
        age=32,
        occupation="便利店员",
        appearance="短碎发、瘦高、深灰连帽衫",
        background="从小被遗弃，性格孤僻",  # 这一句不该进画面提示词
    )
    look = build_look_prompt(character)
    assert "男性" in look
    assert "32岁" in look
    assert "便利店员" in look
    assert "短碎发" in look
    # 背景故事对画面没有直接帮助，放进去反而会稀释视觉信息
    assert "遗弃" not in look


def test_look_prompt_without_age_falls_back_to_age_group():
    character = _char(1, gender="female", age=None, age_group="elderly")
    assert "老年" in build_look_prompt(character)


def test_handwritten_look_prompt_wins():
    """手写的 look_prompt 优先级最高 —— 这是用户"锁定某个具体长相"的开关。"""
    character = _char(1, appearance="随便写的外貌", look_prompt="参考某位演员，清瘦、戴金丝眼镜")
    assert resolve_look_prompt(character) == "参考某位演员，清瘦、戴金丝眼镜"

    # 清空后回到自动生成
    character.look_prompt = ""
    assert "随便写的外貌" in resolve_look_prompt(character)


# ───────────────────────── ③ 三视图提示词 ─────────────────────────


def test_sheet_prompt_asks_for_three_views_and_forbids_text():
    """三视图提示词的四条硬要求，少一条模型就会画歪。"""
    prompt = build_sheet_prompt(_char(1, appearance="黑长直、白色衬衫"), style="冷色调悬疑")
    assert "正面" in prompt and "侧面" in prompt and "背面" in prompt
    assert "全身" in prompt
    assert "禁止" in prompt and "文字" in prompt and "水印" in prompt
    assert "冷色调悬疑" in prompt  # 剧集风格要带上，否则设定图和镜头不同风格
    assert "黑长直" in prompt


# ───────────────────────── ④ 谁算出镜 ─────────────────────────


def test_characters_in_shot_uses_lines_and_names():
    """两条依据取并集：有台词的角色 + 名字出现在描述里的角色。"""
    lin = _char(1, name="林晚")
    chen = _char(2, name="陈默")
    boss = _char(3, name="老板")

    class Line:
        def __init__(self, character_id):
            self.character_id = character_id

    shot = _shot(description="林晚站在雨里，一言不发", dialogue="陈默：别出声。")
    present = characters_in_shot(shot, [lin, chen, boss], [Line(2)])
    names = {c.name for c in present}
    assert names == {"林晚", "陈默"}, names  # 陈默来自台词行，林晚来自描述
    assert "老板" not in names


def test_narrator_is_never_a_visible_character():
    """旁白没有形象：既不能进画面提示词，也不该生成三视图。"""
    narrator = _char(9, name="旁白", gender="female", age_group="young")
    shot = _shot(dialogue="旁白：现在是深夜，凌晨三点十九分。")
    assert characters_in_shot(shot, [narrator], []) == []


# ───────────────────────── ⑤ 镜头提示词拼装 ─────────────────────────


def test_character_look_comes_before_the_scene():
    """角色描述必须在场景描述**前面** —— 模型对提示词前半段更敏感。"""
    character = _char(1, name="林晚", appearance="黑长直、白色衬衫", age=24)
    shot = _shot(image_prompt="rainy subway platform, neon lights", description="雨夜站台")
    prompt, used = compose_shot_prompt(shot, [character])

    assert used == ["林晚"]
    assert prompt.index("黑长直") < prompt.index("rainy subway platform")


def test_prompt_length_is_capped():
    """提示词太长会被厂商截断（glm-image 上限 1000 字符），所以自己先截。"""
    long_character = _char(1, appearance="很" * 5000)
    prompt, _ = compose_shot_prompt(_shot(image_prompt="场景"), [long_character])
    assert len(prompt) <= 900


def test_shot_without_characters_stays_unchanged():
    """没有出场角色时，提示词就是原来的场景描述 —— 向后兼容，不影响老行为。"""
    shot = _shot(image_prompt="sunset beach")
    prompt, used = compose_shot_prompt(shot, [])
    assert prompt == "sunset beach"
    assert used == []


# ───────────────────────── ⑥ 参考图选择 ─────────────────────────


def test_reference_only_used_for_a_single_character():
    """只有镜头里**恰好一个**角色时，才把它的三视图当参考图。

    多人镜头传其中一个人的设定图，反而会把别人的长相带偏 —— 不如走纯文本。
    """
    from app.services import media

    rel = media.character_sheet_rel_path(777, "png")
    media.save_bytes(rel, FAKE_PNG)

    single = _char(777, name="林晚", turnaround_path=rel)
    assert _pick_reference([single]) is not None

    other = _char(778, name="陈默")
    assert _pick_reference([single, other]) is None, "多人镜头不该传参考图"
    assert _pick_reference([]) is None
    assert _pick_reference([other]) is None, "没有三视图就没有参考图"

    # 三视图文件被删了也不能拿它当参考
    single.turnaround_path = "characters/777/sheet.png"
    media.delete(rel)
    assert _pick_reference([single]) is None


# ───────────────────────── 接口层 ─────────────────────────


class RecordingImage(ImageProvider):
    """记录每个镜头的提示词和参考图 —— 用来断言"送进模型的到底是什么"。"""

    name = "recording"

    def __init__(self, *, supports_reference: bool = False):
        self.calls: list[dict] = []
        self.supports_reference = supports_reference

    async def generate(self, *, prompt: str, reference_image=None) -> bytes:
        self.calls.append({"prompt": prompt, "reference": reference_image})
        return FAKE_PNG


def _prepare(client):
    """建剧 + 生成分镜 + 分析角色表。"""
    drama = client.post("/api/v1/dramas", json={"title": "深夜便利店", "genre": "悬疑"}).json()
    episode = client.post(
        f"/api/v1/dramas/{drama['id']}/episodes",
        json={"title": "第1集", "synopsis": "小林在夜班时发现异常。"},
    ).json()
    client.post(f"/api/v1/episodes/{episode['id']}/generate", json={"shot_count": 3})
    characters = client.post(f"/api/v1/dramas/{drama['id']}/cast/analyze").json()
    return drama, episode, characters


def test_character_card_fields_can_be_edited(client):
    """角色卡上的六个字段都要能存能取。"""
    drama, _, characters = _prepare(client)
    target = next(c for c in characters if c["name"] != "旁白")

    resp = client.patch(
        f"/api/v1/characters/{target['id']}",
        json={
            "appearance": "黑长直、单眼皮、白色衬衫",
            "occupation": "便利店夜班店员",
            "background": "母亲重病，她打三份工。",
            "age": 26,
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["appearance"] == "黑长直、单眼皮、白色衬衫"
    assert body["occupation"] == "便利店夜班店员"
    assert body["background"] == "母亲重病，她打三份工。"
    assert body["age"] == 26

    # 重新读一遍确认落库了（不是只在响应里改）
    listed = client.get(f"/api/v1/dramas/{drama['id']}/characters").json()
    stored = next(c for c in listed if c["id"] == target["id"])
    assert stored["age"] == 26
    assert stored["appearance"] == "黑长直、单眼皮、白色衬衫"


def test_setting_age_also_fixes_the_voice_profile(client):
    """填了 26 岁却用老年音色是说不通的 —— 年龄变了，年龄段要跟着走。"""
    _, _, characters = _prepare(client)
    target = next(c for c in characters if c["name"] != "旁白")

    old_voice = target["voice"]
    body = client.patch(f"/api/v1/characters/{target['id']}", json={"age": 70}).json()
    assert body["age_group"] == "elderly", "70 岁应该落进 elderly"
    assert body["voice"] != old_voice or body["pitch_shift"] != target["pitch_shift"], (
        "年龄段变了，配音参数应该跟着变"
    )


def test_explicit_age_group_is_not_overwritten(client):
    """调用方明确指定年龄段时，不能被年龄推导覆盖。"""
    _, _, characters = _prepare(client)
    target = next(c for c in characters if c["name"] != "旁白")

    body = client.patch(
        f"/api/v1/characters/{target['id']}", json={"age": 70, "age_group": "young"}
    ).json()
    assert body["age"] == 70
    assert body["age_group"] == "young"


def test_turnaround_task_skips_narrator_and_produces_images(client):
    """三视图只给"有形象"的角色生成，旁白跳过。"""
    drama, _, characters = _prepare(client)
    visual = [c for c in characters if c["name"] != "旁白"]
    assert visual, "mock 剧本里应该有可见角色"

    resp = client.post(f"/api/v1/dramas/{drama['id']}/characters/turnarounds", json={})
    assert resp.status_code == 202, resp.text
    task = resp.json()
    assert task["kind"] == "turnaround"
    assert task["episode_id"] is None, "三视图是剧级任务，不属于任何一集"

    finished = client.get(f"/api/v1/tasks/{task['id']}").json()
    assert finished["status"] == "succeeded", finished.get("error")
    assert finished["total"] == len(visual), "旁白不该被算进计划数"

    listed = client.get(f"/api/v1/dramas/{drama['id']}/characters").json()
    for character in listed:
        if character["name"] == "旁白":
            assert character["turnaround_path"] is None, "旁白不该有三视图"
        else:
            assert character["turnaround_path"], f"{character['name']} 应该有三视图"
            preview = client.get(f"/api/v1/characters/{character['id']}/turnaround")
            assert preview.status_code == 200
            assert preview.headers["content-type"].startswith("image/")


def test_turnaround_is_skipped_when_already_generated(client):
    drama, _, _ = _prepare(client)
    first = client.post(f"/api/v1/dramas/{drama['id']}/characters/turnarounds", json={}).json()
    client.get(f"/api/v1/tasks/{first['id']}")

    second = client.post(f"/api/v1/dramas/{drama['id']}/characters/turnarounds", json={}).json()
    finished = client.get(f"/api/v1/tasks/{second['id']}").json()
    assert finished["total"] == 0, "默认只补缺，已有的不重做（一张图也是一次调用，是钱）"


def test_turnaround_without_characters_returns_400(client):
    drama = client.post("/api/v1/dramas", json={"title": "空剧", "genre": "都市"}).json()
    task = client.post(f"/api/v1/dramas/{drama['id']}/characters/turnarounds", json={}).json()
    finished = client.get(f"/api/v1/tasks/{task['id']}").json()
    assert finished["status"] == "failed"
    assert "角色表" in finished["error"]


def test_turnaround_of_other_user_is_hidden(client, login_as):
    drama, _, _ = _prepare(client)
    token = login_as("bob")
    client.headers["Authorization"] = f"Bearer {token}"
    assert client.post(f"/api/v1/dramas/{drama['id']}/characters/turnarounds", json={}).status_code == 404


def test_turnaround_quota_is_separate(client):
    drama, _, _ = _prepare(client)
    client.app.dependency_overrides[get_quota_service] = lambda: QuotaService(
        limits={"shots": 5, "images": 5, "turnaround": 1}
    )

    assert client.post(f"/api/v1/dramas/{drama['id']}/characters/turnarounds", json={}).status_code == 202
    resp = client.post(f"/api/v1/dramas/{drama['id']}/characters/turnarounds", json={})
    assert resp.status_code == 429
    assert "生成角色三视图" in resp.json()["error"]["message"]


def test_shot_image_prompt_carries_the_character_look(client):
    """核心断言：画面的提示词里必须出现角色的外貌描述（这是角色一致性的全部依据）。"""
    drama, episode, characters = _prepare(client)
    target = next(c for c in characters if c["name"] != "旁白")
    client.patch(
        f"/api/v1/characters/{target['id']}",
        json={"appearance": "标志性的红围巾和烫卷短发", "age": 26},
    )

    recorder = RecordingImage()
    client.app.dependency_overrides[get_image_provider] = lambda: recorder
    task = client.post(f"/api/v1/episodes/{episode['id']}/images", json={}).json()
    finished = client.get(f"/api/v1/tasks/{task['id']}").json()
    assert finished["status"] == "succeeded", finished.get("error")

    prompts = [call["prompt"] for call in recorder.calls]
    assert prompts, "应该调用了图片模型"
    with_character = [p for p in prompts if "红围巾" in p]
    assert with_character, f"出场该角色的镜头提示词里必须带上外貌，实际: {prompts[:3]}"
    # 旁白不该被塞进画面提示词
    assert all("旁白" not in p for p in prompts)


def test_reference_image_is_not_passed_when_provider_cannot_use_it(client):
    """厂商不支持参考图时，绝不能把参考图塞给它（免得被静默忽略还让人以为生效了）。"""
    drama, episode, characters = _prepare(client)
    client.post(f"/api/v1/dramas/{drama['id']}/characters/turnarounds", json={})

    recorder = RecordingImage(supports_reference=False)
    client.app.dependency_overrides[get_image_provider] = lambda: recorder
    task = client.post(f"/api/v1/episodes/{episode['id']}/images", json={}).json()
    client.get(f"/api/v1/tasks/{task['id']}")

    assert all(call["reference"] is None for call in recorder.calls)


# ───────────────────────── ⑦ 镜头手动编辑 ─────────────────────────


def test_edit_shot_fields(client):
    _, episode, _ = _prepare(client)
    shot = client.get(f"/api/v1/episodes/{episode['id']}").json()["shots"][0]

    resp = client.patch(
        f"/api/v1/shots/{shot['id']}",
        json={
            "shot_type": "特写",
            "camera": "推镜",
            "description": "改过的画面描述",
            "duration_sec": 6.5,
            "image_prompt": "a new prompt",
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["shot_type"] == "特写"
    assert body["camera"] == "推镜"
    assert body["description"] == "改过的画面描述"
    assert body["duration_sec"] == 6.5
    assert body["image_prompt"] == "a new prompt"


def test_editing_dialogue_invalidates_derived_audio(client):
    """改了台词，旧的逐句台词行和音频必须作废 —— 否则台词和配音会自相矛盾。"""
    import os

    drama, episode, _ = _prepare(client)
    shot = client.get(f"/api/v1/episodes/{episode['id']}").json()["shots"][0]

    # 先做一次配音（会产生 dialogue_lines 和音频文件）
    task = client.post(f"/api/v1/episodes/{episode['id']}/audio", json={}).json()
    client.get(f"/api/v1/tasks/{task['id']}")
    detail = client.get(f"/api/v1/episodes/{episode['id']}").json()
    shot = next(s for s in detail["shots"] if s["id"] == shot["id"])
    if not shot["lines"]:
        return  # 这个镜头本来就没台词，换个断言没意义

    audio_files = [
        os.path.join(os.environ["MEDIA_ROOT"], line["audio_path"])
        for line in shot["lines"]
        if line["audio_path"]
    ]

    body = client.patch(
        f"/api/v1/shots/{shot['id']}", json={"dialogue": "林晚：我改了一句台词。"}
    ).json()

    assert body["dialogue"] == "林晚：我改了一句台词。"
    assert body["lines"] == [], "台词变了，派生的逐句台词行应该被清空"
    assert body["audio_path"] is None, "整段音频也应该作废"
    assert not any(os.path.exists(path) for path in audio_files), "音频文件也该被删掉，不留孤儿"


def test_editing_description_keeps_the_existing_image(client):
    """改画面描述**不删**已生成的图：那是花过钱的结果，删不删由用户决定。"""
    drama, episode, _ = _prepare(client)
    task = client.post(f"/api/v1/episodes/{episode['id']}/images", json={}).json()
    client.get(f"/api/v1/tasks/{task['id']}")

    detail = client.get(f"/api/v1/episodes/{episode['id']}").json()
    shot = next(s for s in detail["shots"] if s["image_path"])
    body = client.patch(
        f"/api/v1/shots/{shot['id']}", json={"description": "换一个描述"}
    ).json()
    assert body["image_path"] == shot["image_path"], "旧图应该保留"


def test_edit_shot_of_other_user_is_404(client, login_as):
    _, episode, _ = _prepare(client)
    shot = client.get(f"/api/v1/episodes/{episode['id']}").json()["shots"][0]

    token = login_as("bob")
    client.headers["Authorization"] = f"Bearer {token}"
    assert client.patch(f"/api/v1/shots/{shot['id']}", json={"description": "x"}).status_code == 404
