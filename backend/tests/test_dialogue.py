"""台词解析的测试。

用例全部来自**真实生成的台词**（从开发库里捞出来的），不是凭空编的格式 ——
解析规则一旦和真实数据脱节，配音就会把角色名一起念出来。
"""

import pytest

from app.services.dialogue import parse_dialogue, parse_line, spoken_text


@pytest.mark.parametrize(
    ("raw", "speaker", "text", "cue"),
    [
        # 最常见：角色：台词
        ("林晚：这……这是什么？", "林晚", "这……这是什么？", None),
        # 括号提示 + 角色
        ("（内心独白）林晚：我什么都没做。", "林晚", "我什么都没做。", "内心独白"),
        # 括号本身就是说话人
        ("（来电显示）：林晚，你怎么知道我的名字？", "来电显示", "林晚，你怎么知道我的名字？", "来电显示"),
        # 旁白
        ("旁白：现在是深夜，凌晨三点十九分。", "旁白", "现在是深夜，凌晨三点十九分。", None),
        # 半角冒号
        ("陈默: 签字吧。", "陈默", "签字吧。", None),
        # 冒号后有多余空格
        ("林晚：   你终于来了。", "林晚", "你终于来了。", None),
        # 没有前缀：整行都要读
        ("他站在雨里，一句话也没说。", None, "他站在雨里，一句话也没说。", None),
        # 台词本身含冒号 —— 只按第一个冒号切
        ("医生：情况不太乐观：需要马上手术。", "医生", "情况不太乐观：需要马上手术。", None),
    ],
)
def test_parse_line(raw, speaker, text, cue):
    line = parse_line(raw)
    assert line is not None
    assert line.speaker == speaker
    assert line.text == text
    assert line.cue == cue


def test_role_name_never_ends_up_in_speech():
    """这是整个模块存在的理由：角色名绝不能被念出来。"""
    assert spoken_text("林晚：这……这是什么？") == "这……这是什么？"
    assert "林晚" not in spoken_text("林晚：这……这是什么？")


def test_multiple_speakers_in_one_shot():
    """一个镜头里可能有好几个人说话 —— 配音要逐句换音色。"""
    dialogue = "陈默：签字吧，你母亲的手术费我已经打过去了。\n林晚：为什么是我？"
    lines = parse_dialogue(dialogue)
    assert [line.speaker for line in lines] == ["陈默", "林晚"]
    assert [line.text for line in lines] == [
        "签字吧，你母亲的手术费我已经打过去了。",
        "为什么是我？",
    ]


def test_empty_and_blank_lines_are_skipped():
    assert parse_dialogue("") == []
    assert parse_dialogue(None) == []
    lines = parse_dialogue("\n  \n林晚：在。\n")
    assert len(lines) == 1
    assert lines[0].text == "在。"


def test_long_prefix_is_not_treated_as_speaker():
    """冒号前如果是一整句台词，就不能当人名切掉 —— 否则会丢内容。"""
    raw = "他站在雨里想了很久很久一句话也没说：然后转身走了。"
    line = parse_line(raw)
    assert line.speaker is None
    assert line.text == raw


def test_sentence_punctuation_before_colon_is_not_a_speaker():
    raw = "这是真的吗？：不像。"
    line = parse_line(raw)
    assert line.speaker is None


def test_bare_speaker_prefix_is_dropped():
    """`林晚：` 后面什么都没有 —— 没有可朗读的内容。

    如果照原样送去合成，TTS 会把"林晚"念出来。所以宁可丢掉这一句。
    """
    assert parse_line("林晚：") is None
    assert parse_line("（内心独白）林晚：  ") is None
    # 但内容很长的行不能被误丢
    assert parse_line("他想了很久：然后转身走了。") is not None


def test_parse_line_strips_whitespace_only_lines():
    assert parse_line("   ") is None
    assert parse_line("\t\n") is None


def test_raw_is_preserved_for_debugging():
    line = parse_line("  （低声）林晚：别出声。  ")
    assert line.raw == "  （低声）林晚：别出声。  "
    assert line.cue == "低声"
    assert line.speaker == "林晚"
    assert line.text == "别出声。"
