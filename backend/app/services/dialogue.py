"""台词解析：把「谁说的」和「说了什么」分开。

**为什么必须有这一层**：
分镜里的台词是 LLM 按剧本格式写的，形如 `林晚：这……这是什么？`。
如果整行直接丢给 TTS，观众会听到"林晚，这……这是什么？"——
把角色名也念出来了，非常出戏。

真实数据长这样（来自我们自己的分镜生成，不是凭空假设）：
    林晚：这……这是什么？
    （内心独白）林晚：我什么都没做。
    （来电显示）：林晚，你怎么知道我的名字？
    旁白：现在是深夜，凌晨三点十九分。
所以要处理四种情况：
    1. `角色：内容`        -> speaker=角色, text=内容
    2. `（提示）角色：内容`  -> cue=提示, speaker=角色, text=内容
    3. `（提示）：内容`      -> cue=提示, speaker=提示, text=内容
    4. `内容`（没有前缀）    -> speaker=None, text=内容
第 2 种里的括号是**表演提示**（内心独白/来电显示/低声…），
它是很好的语气线索，所以单独留下来给后面的情感分析用，不参与合成。
"""

import re
from dataclasses import dataclass

#: 说话人名字最长多少字（超过这个长度就不像人名，更像台词本身）
SPEAKER_MAX_LEN = 12

#: 句末标点 —— 出现在"冒号前"就说明那段其实是台词，不是人名
_SENTENCE_PUNCT = "。！？…；;"

#: 开头的括号提示，全角半角都认
_PAREN_PREFIX = re.compile(r"^\s*[（(]\s*([^）)]{1,20}?)\s*[）)]\s*")

#: `说话人：内容`（全角冒号、半角冒号、可带空格）
_SPEAKER = re.compile(r"^([^：:]{1,20}?)\s*[：:]\s*(.+)$", re.DOTALL)


@dataclass(frozen=True)
class DialogueLine:
    """一句台词。"""

    text: str  # 真正要合成的内容（不含角色名和括号提示）
    speaker: str | None = None  # 谁说的；None = 没写说话人（多半是旁白/画面描述）
    cue: str | None = None  # 括号里的表演提示，如 "内心独白"、"来电显示"
    raw: str = ""  # 原始整行，便于排查和前端展示


def parse_line(chunk: str) -> DialogueLine | None:
    """解析一行。空白行返回 None。"""
    raw = chunk
    text = chunk.strip()
    if not text:
        return None

    # ① 先摘掉开头的括号提示
    cue: str | None = None
    match = _PAREN_PREFIX.match(text)
    if match:
        cue = match.group(1).strip()
        text = text[match.end() :].strip()

    # ② 形如 `（来电显示）：台词` —— 括号里的内容本身就是说话人
    if cue and text[:1] in ("：", ":"):
        return DialogueLine(text=text[1:].strip(), speaker=cue, cue=cue, raw=raw)

    # ③ 形如 `角色：台词`
    match = _SPEAKER.match(text)
    if match:
        candidate = match.group(1).strip()
        body = match.group(2).strip()
        if candidate and len(candidate) <= SPEAKER_MAX_LEN and not any(
            ch in candidate for ch in _SENTENCE_PUNCT
        ):
            return DialogueLine(text=body, speaker=candidate, cue=cue, raw=raw)

    # ④ 没有前缀：整行都是要读的内容
    if text.endswith(("：", ":")) and len(text) <= SPEAKER_MAX_LEN + 1:
        # 形如 "林晚：" —— 只有说话人、没有内容。
        # 这种行**没有可朗读的东西**，但如果照原样送去合成，就会把角色名念出来，
        # 所以直接丢掉（宁可少一句，也不要出戏）。
        return None

    return DialogueLine(text=text, speaker=None, cue=cue, raw=raw)


def parse_dialogue(dialogue: str | None) -> list[DialogueLine]:
    """把一个镜头的台词解析成若干句（一个镜头里可能有多个人说话）。"""
    lines: list[DialogueLine] = []
    for chunk in (dialogue or "").splitlines():
        parsed = parse_line(chunk)
        if parsed is not None:
            lines.append(parsed)
    return lines


def spoken_text(dialogue: str | None) -> str:
    """只取"要读出来的内容"，用换行拼回去（排查问题时很好用）。"""
    return "\n".join(line.text for line in parse_dialogue(dialogue))
