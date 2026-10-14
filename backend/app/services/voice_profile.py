"""音色档案：把「角色的性别 + 年龄段」翻译成「音色 + 音高偏移 + 语速」。

**关键背景（这是实测数据，不是猜的）**
智谱 GLM-TTS 可用的音色只有 7 个。我听不到音频，所以用自相关算法测了每个音色的基频（F0）：

    xiaochen  134 Hz  -> 男声（成年男性典型范围 85~155 Hz）
    tongtong  183 Hz  |  kazi  183 Hz       -> 女声（偏低）
    jam       238 Hz  |  chuichui 245 Hz    -> 女声
    luodo     247 Hz  |  douji  316 Hz      -> 女声 / 童声感

结论：**能用的男声只有 1 个（xiaochen）**。
但短剧里男性角色往往不止一个（年轻男主 + 中年反派 + 老年长辈），只靠换音色不够。

**解决办法：同一个音色做音高偏移。**
    变调由 ffmpeg 完成：asetrate 改变采样率（变调）→ aresample 还原采样率 → atempo 反向补偿（保持时长）。
    这样"年轻男 / 中年男 / 年老男"就有区别了，而且语速再叠一层（老年人说话本来就慢）。

**这份表格是可以改的**：标签里的"年龄感"是我根据基频推的，
真实听感只有耳朵能判断。所以前端会把它做成可编辑，听不顺耳直接改。
"""

from dataclasses import dataclass

#: 兜底音色（也是默认音色，女声）
DEFAULT_VOICE = "tongtong"

#: 实测到的音色基频，展示给用户帮助选角（也是我写这套映射的依据）
VOICE_PITCH_HZ: dict[str, float] = {
    "xiaochen": 134.1,
    "tongtong": 183.2,
    "kazi": 183.2,
    "jam": 237.6,
    "chuichui": 244.9,
    "luodo": 247.4,
    "douji": 315.8,
}

#: 音色的推断标签 —— 用于前端下拉框显示，帮助用户理解每个音色大概是什么感觉
VOICE_LABEL: dict[str, str] = {
    "xiaochen": "小陈（男声 134Hz）",
    "tongtong": "彤彤（女声 183Hz，默认）",
    "kazi": "kazi（女声 183Hz）",
    "jam": "jam（女声 238Hz）",
    "chuichui": "锤锤（女声 245Hz）",
    "luodo": "luodo（女声 247Hz）",
    "douji": "douji（偏高 316Hz，适合童声）",
}

GENDERS = ("male", "female", "unknown")
AGE_GROUPS = ("child", "teen", "young", "middle", "elderly")


@dataclass(frozen=True)
class VoiceProfile:
    """一套配音参数。"""

    voice: str
    pitch_shift: float  # 0.06 = 升高 6%（更年轻/更尖）；-0.09 = 降低 9%（更年长/更沉）
    speed: float  # 基础语速（情绪会在它之上再乘一个系数）
    label: str  # 中文说明，方便排查和前端展示


#: (性别, 年龄段) -> 配音参数
_PROFILES: dict[tuple[str, str], VoiceProfile] = {
    ("male", "child"): VoiceProfile("xiaochen", 0.20, 1.10, "男童"),
    ("male", "teen"): VoiceProfile("xiaochen", 0.14, 1.08, "少年男"),
    ("male", "young"): VoiceProfile("xiaochen", 0.06, 1.04, "年轻男性"),
    ("male", "middle"): VoiceProfile("xiaochen", 0.0, 0.98, "中年男性"),
    ("male", "elderly"): VoiceProfile("xiaochen", -0.09, 0.88, "年老男性"),
    ("female", "child"): VoiceProfile("douji", 0.0, 1.05, "女童"),
    ("female", "teen"): VoiceProfile("chuichui", 0.06, 1.05, "少女"),
    ("female", "young"): VoiceProfile("tongtong", 0.04, 1.02, "年轻女性"),
    ("female", "middle"): VoiceProfile("tongtong", 0.0, 0.97, "中年女性"),
    ("female", "elderly"): VoiceProfile("kazi", -0.07, 0.88, "年老女性"),
    ("unknown", "child"): VoiceProfile("chuichui", 0.08, 1.05, "性别未定（童声）"),
    ("unknown", "teen"): VoiceProfile("tongtong", 0.05, 1.02, "性别未定（少年）"),
    ("unknown", "young"): VoiceProfile("tongtong", 0.0, 1.0, "性别未定"),
    ("unknown", "middle"): VoiceProfile("kazi", 0.0, 0.97, "性别未定"),
    ("unknown", "elderly"): VoiceProfile("kazi", -0.07, 0.88, "性别未定（年长）"),
}

#: 旁白专用：略微压低 + 放慢，听感上更"讲故事"
NARRATOR_PROFILE = VoiceProfile("tongtong", -0.04, 0.95, "旁白")

#: 台词里可能出现的"旁白"写法
NARRATOR_NAMES = ("旁白", "画外音", "字幕", "解说")


def normalize_gender(value: str | None) -> str:
    return value if value in GENDERS else "unknown"


def normalize_age_group(value: str | None) -> str:
    return value if value in AGE_GROUPS else "young"


def profile_for(gender: str | None, age_group: str | None) -> VoiceProfile:
    """按性别 + 年龄段取配音参数，取不到就退回"性别未定/年轻"。"""
    key = (normalize_gender(gender), normalize_age_group(age_group))
    return _PROFILES.get(key, _PROFILES[("unknown", "young")])


def profile_for_character(character) -> VoiceProfile:
    """角色对象 -> 配音参数。

    角色表里存的 voice/pitch_shift/speed 是**可编辑的最终值**；
    如果用户（或旧数据）没填，就按性别年龄推一套出来。
    """
    if not character.voice:
        return NARRATOR_PROFILE if character.name in NARRATOR_NAMES else profile_for(
            character.gender, character.age_group
        )
    return VoiceProfile(
        voice=character.voice,
        pitch_shift=character.pitch_shift,
        speed=character.speed,
        label=f"{character.name}（自定义）",
    )


def is_narrator(name: str | None) -> bool:
    return bool(name) and any(token in name for token in NARRATOR_NAMES)
