"""多角色配音的测试：角色表、情感分析、逐句合成。

这组测试守护的是用户最在意的三件事：
    ① 角色名不能被念出来（`林晚：这……这是什么？` 只读后半句）
    ② 不同角色要发出不同的声音（男/女、不同年龄）
    ③ 语气要跟着情境变（急迫的句子语速更快）
"""

import asyncio
import json

from app.api.deps import get_llm_provider, get_tts_provider, get_quota_service
from app.services.llm.base import LLMProvider
from app.services.quota import QuotaService
from app.services.tts.base import TtsProvider
from app.services.tts.mock import MockTtsProvider

# 一个镜头里两个人对话 —— 这正是"整段一个音色"会露馅的场景
TWO_SPEAKER_SHOTS = [
    {
        "shot_type": "中景",
        "camera": "固定",
        "description": "小林站在收银台后，戴帽子的人走近",
        "dialogue": "林晚：这……这是什么？\n陈默：别出声。",
        "duration_sec": 4,
        "image_prompt": "night convenience store, two people",
    }
]


class ScriptedLLM(LLMProvider):
    """按 system 提示词的内容返回不同的假响应。

    真实项目里这三类调用会打到同一个模型，但提示词不同；
    测试里就靠提示词里的特征词分发。
    """

    name = "scripted"

    def __init__(self, *, shots=None, characters=None, lines=None):
        self.shots = shots if shots is not None else TWO_SPEAKER_SHOTS
        self.characters = characters or []
        self.lines = lines or []
        self.calls: list[str] = []

    async def complete(self, *, system: str, user: str) -> str:
        if "选角表" in system:
            self.calls.append("cast")
            return json.dumps({"characters": self.characters}, ensure_ascii=False)
        if "情绪和语速" in system:
            self.calls.append("emotion")
            return json.dumps({"lines": self.lines}, ensure_ascii=False)
        self.calls.append("script")
        return json.dumps({"title": "第一集", "shots": self.shots}, ensure_ascii=False)


class RecordingTts(TtsProvider):
    """记录每一次调用的 TTS Provider —— 让我们能断言"送进去的到底是什么"。"""

    name = "recording"
    extension = "wav"

    def __init__(self):
        self.calls: list[dict] = []

    async def synthesize(self, *, text: str, style: str = "", voice=None, speed=None) -> bytes:
        self.calls.append({"text": text, "voice": voice, "speed": speed})
        return await MockTtsProvider().synthesize(text=text, voice=voice, speed=speed)


def _prepare(client, llm: ScriptedLLM, tts: RecordingTts | None = None):
    client.app.dependency_overrides[get_llm_provider] = lambda: llm
    if tts is not None:
        client.app.dependency_overrides[get_tts_provider] = lambda: tts

    drama = client.post("/api/v1/dramas", json={"title": "深夜便利店", "genre": "悬疑"}).json()
    episode = client.post(
        f"/api/v1/dramas/{drama['id']}/episodes",
        json={"title": "第1集", "synopsis": "小林发现每晚三点都有人来买同一瓶牛奶。"},
    ).json()
    client.post(f"/api/v1/episodes/{episode['id']}/generate", json={"shot_count": 1})
    return drama, episode


def _run_audio(client, episode_id, only_missing=True):
    resp = client.post(f"/api/v1/episodes/{episode_id}/audio", json={"only_missing": only_missing})
    assert resp.status_code == 202, resp.text
    task = resp.json()
    return client.get(f"/api/v1/tasks/{task['id']}").json()


CAST = [
    {"name": "林晚", "gender": "female", "age_group": "young", "personality": "敏感压抑", "role": "主角"},
    {"name": "陈默", "gender": "male", "age_group": "middle", "personality": "沉稳寡言", "role": "配角"},
]


# ───────────────────────── ① 角色名不能被念出来 ─────────────────────────


def test_character_name_is_never_sent_to_tts(client):
    """`林晚：这……这是什么？` 送进 TTS 的必须只有后半句。"""
    tts = RecordingTts()
    drama, episode = _prepare(client, ScriptedLLM(characters=CAST), tts)

    finished = _run_audio(client, episode["id"])
    assert finished["status"] == "succeeded", finished.get("error")

    spoken = [call["text"] for call in tts.calls]
    assert "这……这是什么？" in spoken
    assert "别出声。" in spoken
    # 这是整个功能存在的理由
    assert all("林晚" not in text and "陈默" not in text for text in spoken), spoken
    assert all("：" not in text for text in spoken), spoken
    del drama


# ───────────────────────── ② 不同角色不同声音 ─────────────────────────


def test_each_character_gets_its_own_voice(client):
    tts = RecordingTts()
    _, episode = _prepare(client, ScriptedLLM(characters=CAST), tts)
    assert _run_audio(client, episode["id"])["status"] == "succeeded"

    by_text = {call["text"]: call for call in tts.calls}
    female_line = by_text["这……这是什么？"]  # 林晚（女）
    male_line = by_text["别出声。"]  # 陈默（男）

    assert female_line["voice"] != male_line["voice"], "男女角色必须是不同音色"
    assert male_line["voice"] == "xiaochen", "男性角色应分配到实测的男声 xiaochen"
    # 中年男性比年轻女性说得慢一点
    assert male_line["speed"] < female_line["speed"]


def test_cast_is_analyzed_automatically_when_missing(client):
    """直接点配音、还没有角色表时，任务里会自动先分析一次。"""
    llm = ScriptedLLM(characters=CAST)
    _, episode = _prepare(client, llm, RecordingTts())

    assert _run_audio(client, episode["id"])["status"] == "succeeded"
    assert "cast" in llm.calls

    characters = client.get(f"/api/v1/dramas/{episode['drama_id']}/characters").json()
    assert {c["name"] for c in characters} == {"林晚", "陈默"}


# ───────────────────────── ③ 语气跟着情境变 ─────────────────────────


def test_emotion_changes_speed(client):
    """同一句台词，标成"急迫"就该比标成"平静"读得快。"""
    lines = [
        {"shot": 1, "line": 0, "speaker": "林晚", "emotion": "惊恐", "intensity": 1.0, "pace": "fast"},
        {"shot": 1, "line": 1, "speaker": "陈默", "emotion": "平静", "intensity": 0.2, "pace": "slow"},
    ]
    tts = RecordingTts()
    _, episode = _prepare(client, ScriptedLLM(characters=CAST, lines=lines), tts)
    assert _run_audio(client, episode["id"])["status"] == "succeeded"

    by_text = {call["text"]: call for call in tts.calls}
    scared = by_text["这……这是什么？"]  # 惊恐 + fast + 强
    calm = by_text["别出声。"]  # 平静 + slow + 弱

    assert scared["speed"] > calm["speed"]

    detail = client.get(f"/api/v1/episodes/{episode['id']}").json()
    emotions = {line["text"]: line["emotion"] for line in detail["shots"][0]["lines"]}
    assert emotions["这……这是什么？"] == "惊恐"
    assert emotions["别出声。"] == "平静"


def test_emotion_analysis_runs_only_once(client):
    """情绪分析要花钱，重跑配音不该重复分析同样的句子。

    注意这里给**所有**台词都标了情绪：如果模型漏报了几句，
    下一次配音会补分析那几句（这是有意的重试，不是 bug），
    所以"不重复花钱"针对的是"已经分析完整的镜头"。
    """
    llm = ScriptedLLM(
        characters=CAST,
        lines=[
            {"shot": 1, "line": 0, "emotion": "紧张", "intensity": 0.7, "pace": "fast"},
            {"shot": 1, "line": 1, "emotion": "平静", "intensity": 0.3, "pace": "normal"},
        ],
    )
    _, episode = _prepare(client, llm, RecordingTts())

    _run_audio(client, episode["id"])
    first = llm.calls.count("emotion")
    assert first == 1
    _run_audio(client, episode["id"], only_missing=False)
    assert llm.calls.count("emotion") == first, "第二次不该再调用情绪分析"


# ───────────────────────── 逐句落库 ─────────────────────────


def test_dialogue_lines_are_persisted_with_speaker(client):
    _, episode = _prepare(client, ScriptedLLM(characters=CAST), RecordingTts())
    assert _run_audio(client, episode["id"])["status"] == "succeeded"

    detail = client.get(f"/api/v1/episodes/{episode['id']}").json()
    lines = detail["shots"][0]["lines"]
    assert [line["speaker"] for line in lines] == ["林晚", "陈默"]
    assert [line["text"] for line in lines] == ["这……这是什么？", "别出声。"]
    assert all(line["audio_path"] for line in lines), "每句都应该有自己的音频"


def test_shot_audio_is_the_concatenation_of_lines(client):
    """镜头音频要覆盖两句台词的时间（拼接了，而不是只读了第一句）。"""
    _, episode = _prepare(client, ScriptedLLM(characters=CAST), RecordingTts())
    assert _run_audio(client, episode["id"])["status"] == "succeeded"

    detail = client.get(f"/api/v1/episodes/{episode['id']}").json()
    shot_id = detail["shots"][0]["id"]
    audio = client.get(f"/api/v1/shots/{shot_id}/audio")
    assert audio.status_code == 200
    # 两句各约 1 秒，拼接后明显长于单句（WAV 是定长采样，用字节数粗判）
    assert len(audio.content) > 60_000


# ───────────────────────── 角色表接口 ─────────────────────────


def test_cast_endpoint_requires_dialogue(client):
    """还没生成分镜就想分析角色 -> 明确报错。"""
    drama = client.post("/api/v1/dramas", json={"title": "空剧", "genre": "都市"}).json()
    resp = client.post(f"/api/v1/dramas/{drama['id']}/cast/analyze")
    assert resp.status_code == 400
    assert "分镜" in resp.json()["error"]["message"]


def test_cast_endpoint_lists_characters_with_voice(client):
    _, episode = _prepare(client, ScriptedLLM(characters=CAST), RecordingTts())
    resp = client.post(f"/api/v1/dramas/{episode['drama_id']}/cast/analyze")
    assert resp.status_code == 200, resp.text

    characters = {c["name"]: c for c in resp.json()}
    assert characters["陈默"]["gender"] == "male"
    assert characters["陈默"]["voice"] == "xiaochen"
    assert characters["林晚"]["voice"] != characters["陈默"]["voice"]


def test_patch_character_reassigns_voice_when_gender_changes(client):
    """AI 猜错性别时，用户改成女性，音色要跟着换 —— 否则会出现"女性角色男声"。"""
    _, episode = _prepare(client, ScriptedLLM(characters=CAST), RecordingTts())
    characters = {c["name"]: c for c in client.post(f"/api/v1/dramas/{episode['drama_id']}/cast/analyze").json()}

    chen = characters["陈默"]
    assert chen["voice"] == "xiaochen"

    resp = client.patch(f"/api/v1/characters/{chen['id']}", json={"gender": "female"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["gender"] == "female"
    assert body["voice"] != "xiaochen", "改了性别就该重新分配音色"


def test_patch_character_keeps_custom_voice(client):
    """用户明确指定音色时，不要被推导逻辑覆盖掉。"""
    _, episode = _prepare(client, ScriptedLLM(characters=CAST), RecordingTts())
    characters = {c["name"]: c for c in client.post(f"/api/v1/dramas/{episode['drama_id']}/cast/analyze").json()}

    lin = characters["林晚"]
    resp = client.patch(f"/api/v1/characters/{lin['id']}", json={"voice": "chuichui", "speed": 1.2})
    assert resp.status_code == 200
    assert resp.json()["voice"] == "chuichui"
    assert resp.json()["speed"] == 1.2


def test_patch_character_rejects_unknown_voice(client):
    _, episode = _prepare(client, ScriptedLLM(characters=CAST), RecordingTts())
    characters = client.post(f"/api/v1/dramas/{episode['drama_id']}/cast/analyze").json()
    resp = client.patch(f"/api/v1/characters/{characters[0]['id']}", json={"voice": "不存在的音色"})
    assert resp.status_code == 404


def test_other_user_cannot_touch_characters(client, login_as):
    _, episode = _prepare(client, ScriptedLLM(characters=CAST), RecordingTts())
    characters = client.post(f"/api/v1/dramas/{episode['drama_id']}/cast/analyze").json()

    token = login_as("bob")
    client.headers["Authorization"] = f"Bearer {token}"
    assert client.patch(f"/api/v1/characters/{characters[0]['id']}", json={"gender": "male"}).status_code == 404
    assert client.get(f"/api/v1/dramas/{episode['drama_id']}/characters").status_code == 404


def test_voice_list_exposes_measured_pitch(client):
    """音色列表要带上实测基频 —— 用户选角时需要客观依据。"""
    voices = client.get("/api/v1/characters/voices").json()
    by_name = {v["voice"]: v for v in voices}
    assert by_name["xiaochen"]["pitch_hz"] < 160, "小陈是男声，基频应偏低"
    assert by_name["douji"]["pitch_hz"] > 300


def test_cast_quota_is_enforced(client):
    client.app.dependency_overrides[get_quota_service] = lambda: QuotaService(
        limits={"shots": 5, "cast": 1}
    )
    _, episode = _prepare(client, ScriptedLLM(characters=CAST), RecordingTts())

    assert client.post(f"/api/v1/dramas/{episode['drama_id']}/cast/analyze").status_code == 200
    resp = client.post(f"/api/v1/dramas/{episode['drama_id']}/cast/analyze")
    assert resp.status_code == 429
    assert "分析角色" in resp.json()["error"]["message"]


def test_dubbing_failure_is_per_line(client):
    """某一句合成失败，其它句仍然保留。"""

    class FlakyTts(TtsProvider):
        name = "flaky"
        extension = "wav"

        def __init__(self):
            self.count = 0

        async def synthesize(self, *, text, style="", voice=None, speed=None) -> bytes:
            self.count += 1
            if self.count == 1:
                raise RuntimeError("模拟某一句合成失败")
            return await MockTtsProvider().synthesize(text=text, voice=voice, speed=speed)

    _, episode = _prepare(client, ScriptedLLM(characters=CAST), FlakyTts())
    finished = _run_audio(client, episode["id"])
    assert finished["status"] == "succeeded"

    detail = client.get(f"/api/v1/episodes/{episode['id']}").json()
    lines = detail["shots"][0]["lines"]
    assert any(line["audio_error"] for line in lines)
    assert any(line["audio_path"] for line in lines), "另一句应该成功"


def test_mock_tts_honours_speed_and_voice():
    """mock 也要体现语速和音色 —— 否则这两条逻辑在 mock 阶段根本测不出来。"""
    provider = MockTtsProvider()

    fast = asyncio.run(provider.synthesize(text="这是一句测试台词", voice="xiaochen", speed=1.6))
    slow = asyncio.run(provider.synthesize(text="这是一句测试台词", voice="xiaochen", speed=0.7))
    other_voice = asyncio.run(provider.synthesize(text="这是一句测试台词", voice="tongtong", speed=1.0))

    assert len(fast) < len(slow), "语速快 -> 音频更短"
    assert fast != other_voice, "换了音色应该有区别"


# ───────────────────────── 裸台词（没写说话人）的回归测试 ─────────────────────────


def test_bare_dialogue_line_gets_speaker_from_context(client):
    """没写说话人的台词，必须由上下文推断出说话人，而不是默认塞给旁白（女声）。

    这是真实 bug 的回归测试：DeepSeek 有时会生成不带"角色名："前缀的台词，
    而"我找了你好久"从剧情看是男主说的，必须配男声。
    """
    shots = [
        {
            "shot_type": "中景",
            "camera": "固定",
            "description": "陈默走近林晚，语气压抑",
            "dialogue": "我找了你好久。",  # 裸台词，没有说话人
            "duration_sec": 4,
            "image_prompt": "x",
        }
    ]
    tts = RecordingTts()
    llm = ScriptedLLM(
        shots=shots,
        characters=[
            {"name": "陈默", "gender": "male", "age_group": "young", "personality": "沉稳寡言", "role": "主角"}
        ],
        # 情绪分析这一步，模型结合上下文推断出是陈默在说话
        lines=[
            {"shot": 1, "line": 0, "speaker": "陈默", "emotion": "压抑", "intensity": 0.7, "pace": "normal"}
        ],
    )
    _, episode = _prepare(client, llm, tts)
    assert _run_audio(client, episode["id"])["status"] == "succeeded"

    call = tts.calls[0]
    assert call["text"] == "我找了你好久。"
    assert call["voice"] == "xiaochen", f"男主台词不该用女声，实际 {call['voice']}"


def test_bare_line_falls_back_to_previous_speaker(client):
    """模型也没推断出说话人时，裸台词延续同一镜头里上一句的说话人。"""
    shots = [
        {
            "shot_type": "中景",
            "camera": "固定",
            "description": "两人低声对话",
            "dialogue": "陈默：先跟我走。\n别回头。",  # 第二句是裸台词
            "duration_sec": 5,
            "image_prompt": "x",
        }
    ]
    tts = RecordingTts()
    llm = ScriptedLLM(
        shots=shots,
        characters=[
            {"name": "陈默", "gender": "male", "age_group": "young", "personality": "沉稳", "role": "主角"}
        ],
        # 模型没给第二句推断说话人（speaker 留空）
        lines=[
            {"shot": 1, "line": 0, "speaker": "陈默", "emotion": "平静", "intensity": 0.5, "pace": "normal"},
            {"shot": 1, "line": 1, "speaker": "", "emotion": "平静", "intensity": 0.5, "pace": "normal"},
        ],
    )
    _, episode = _prepare(client, llm, tts)
    assert _run_audio(client, episode["id"])["status"] == "succeeded"

    by_text = {call["text"]: call for call in tts.calls}
    assert by_text["别回头。"]["voice"] == "xiaochen", "裸台词应延续上一句（陈默）的音色"
