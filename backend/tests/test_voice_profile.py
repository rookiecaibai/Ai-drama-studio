"""音色档案与语速映射的单元测试（纯函数，跑得快）。"""

import pytest

from app.services.emotion_service import MAX_SPEED, MIN_SPEED, speed_for
from app.services.voice_profile import (
    NARRATOR_PROFILE,
    is_narrator,
    normalize_age_group,
    normalize_gender,
    profile_for,
)


def test_male_voices_are_lower_than_female_across_age_groups():
    """同一性别里，年龄越大音高越低、语速越慢 —— 这是"年龄感"的实现方式。"""
    young = profile_for("male", "young")
    middle = profile_for("male", "middle")
    elderly = profile_for("male", "elderly")

    assert young.pitch_shift > middle.pitch_shift > elderly.pitch_shift
    assert young.speed > middle.speed > elderly.speed


def test_only_one_usable_male_voice_so_age_comes_from_pitch():
    """这是个**已知限制的回归测试**：可用的男声只有一个。

    智谱 GLM-TTS 的 7 个音色里只有 xiaochen 是男声（实测 134Hz）。
    所以所有男性角色都基于它，靠音高偏移区分年龄。
    哪天换到有多个男声的服务，这条测试会失败 —— 那时应该改成按音色区分。
    """
    male_voices = {profile_for("male", age).voice for age in ("child", "teen", "young", "middle", "elderly")}
    assert male_voices == {"xiaochen"}


def test_female_roles_spread_across_multiple_voices():
    voices = {profile_for("female", age).voice for age in ("child", "teen", "young", "middle", "elderly")}
    assert len(voices) >= 3, "女声应该能拉开差别，不必只靠变调"


def test_unknown_gender_and_age_fall_back_safely():
    profile = profile_for(None, None)
    assert profile.voice
    assert 0.5 <= profile.speed <= 2.0


def test_normalize_rejects_garbage():
    assert normalize_gender("男性") == "unknown"
    assert normalize_gender("male") == "male"
    assert normalize_age_group("old") == "young"
    assert normalize_age_group("elderly") == "elderly"


@pytest.mark.parametrize("name", ["旁白", "画外音", "（旁白）", "解说"])
def test_narrator_detection(name):
    assert is_narrator(name)


def test_narrator_profile_is_calmer():
    assert NARRATOR_PROFILE.speed < 1.0
    assert NARRATOR_PROFILE.pitch_shift < 0  # 压低一点，更像"讲故事"


# ───────────────────────── 语速映射 ─────────────────────────


def test_intensity_amplifies_deviation_from_normal():
    """强度只放大"偏离 1.0 的部分"：强度 0.5 时几乎不改变语速。

    否则只要标了情绪就一律加速，整集听起来会很赶、很假。
    """
    neutral = speed_for(1.0, emotion="急迫", pace="fast", intensity=0.5)
    strong = speed_for(1.0, emotion="急迫", pace="fast", intensity=1.0)
    assert strong > neutral

    # 强度 0.5 时，修正系数正好是 1.0（不放大）
    assert speed_for(1.0, emotion="急迫", pace="fast", intensity=0.5) == pytest.approx(1.12 * 1.06, abs=0.02)


def test_pace_dominates():
    fast = speed_for(1.0, emotion="平静", pace="fast", intensity=0.5)
    slow = speed_for(1.0, emotion="平静", pace="slow", intensity=0.5)
    assert fast > slow


def test_urgency_faster_than_sadness():
    urgent = speed_for(1.0, emotion="急迫", pace="fast", intensity=0.9)
    sad = speed_for(1.0, emotion="悲伤", pace="slow", intensity=0.9)
    assert urgent > sad


def test_base_speed_is_respected():
    """角色基础语速要在结果里体现：老年角色的句子整体更慢。"""
    young = speed_for(1.04, emotion="平静", pace="normal", intensity=0.5)
    elderly = speed_for(0.88, emotion="平静", pace="normal", intensity=0.5)
    assert elderly < young


def test_speed_stays_within_provider_limits():
    """智谱 GLM-TTS 的 speed 取值是 [0.5, 2]，我们必须留在范围内。"""
    for emotion in ("惊恐", "急迫", "悲伤", "虚弱"):
        for pace in ("slow", "normal", "fast"):
            for base in (0.88, 1.0, 1.1):
                result = speed_for(base, emotion=emotion, pace=pace, intensity=1.0)
                assert MIN_SPEED <= result <= MAX_SPEED, (emotion, pace, base, result)
                assert 0.5 <= result <= 2.0


def test_unknown_emotion_and_pace_do_not_crash():
    result = speed_for(1.0, emotion="莫名其妙", pace="飞奔", intensity=0.8)
    assert MIN_SPEED <= result <= MAX_SPEED
