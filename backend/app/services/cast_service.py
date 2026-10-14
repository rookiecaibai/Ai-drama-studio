"""用大模型分析出这部剧的「角色表」—— 配音的选角依据。

**为什么先要角色表**
一个镜头里可能有多个人说话（`陈默：…` / `林晚：…`）。
要让每个人发出自己的声音，先得知道"谁是谁"：性别、年龄段、说话风格。
这些信息 AI 能从剧本里读出来 —— 但它会猜错（名字看不出性别、年龄只能靠剧情推测），
所以结果是**可编辑的数据**（存在 characters 表），不是写死的常量。

**为什么把"真实出现过的说话人"喂给模型**
如果只给剧本让它自由发挥，它会编出一些根本没说过话的角色，
同时漏掉只有一两句台词的龙套。所以流程是：
先由解析层从台词里抽出**真实的说话人清单**，再让模型只对这些人生成档案。
"""

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.db.models import Character, Drama, Episode, Shot
from app.services.dialogue import parse_dialogue
from app.services.llm.base import LLMProvider
from app.services.llm.parsing import extract_json
from app.services.voice_profile import (
    NARRATOR_PROFILE,
    is_narrator,
    normalize_age_group,
    normalize_gender,
    profile_for,
)

logger = logging.getLogger(__name__)

#: 台词里没有写说话人时，当作旁白处理
DEFAULT_SPEAKER = "旁白"

CAST_SYSTEM_PROMPT = """你是短剧的配音导演。现在要为一个剧本做「选角表」。

请阅读剧本信息，为每个角色判断下面几项：

- name：角色名。**必须和台词里冒号前的写法完全一致**（台词是"林晚：…"，name 就写"林晚"）。
- gender：male（男） / female（女） / unknown（判断不了）
- age_group：child（儿童0-12） / teen（少年13-17） / young（青年18-30） /
  middle（中年31-55） / elderly（老年56+）
- personality：一句话描述这个角色的性格和说话风格（40 字以内）。
  这一项会被后续的"语气分析"用到，所以要写具体的表达习惯，
  例如"外表温柔、说话慢，但关键时刻很坚决"。
- role：主角 / 配角 / 龙套

判断依据：名字本身、别人对他的称呼、剧情中的身份关系、说话方式。

只输出 JSON，不要任何解释：
{"characters": [{"name": "林晚", "gender": "female", "age_group": "young",
  "personality": "敏感压抑，说话轻声但倔强", "role": "主角"}]}

注意：
1. **只给下面列出的说话人生成档案**，不要凭空增加角色。
2. 名单里的每一个都要有，一个都不能漏（哪怕只有一句台词）。
3. "旁白"/"画外音"这类也要列出来，性别年龄按叙述者的感觉给。""".strip()


def collect_speakers(db: Session, drama: Drama) -> dict[str, list[str]]:
    """从这部剧所有已生成的台词里，抽出真实出现过的说话人和几句样例。

    返回 {说话人名: [台词样例…]}，样例用于让模型判断这个人的性别/年龄/说话风格。
    """
    dialogues = list(
        db.scalars(
            select(Shot.dialogue)
            .join(Episode, Shot.episode_id == Episode.id)
            .where(Episode.drama_id == drama.id)
        )
    )

    speakers: dict[str, list[str]] = {}
    for dialogue in dialogues:
        for line in parse_dialogue(dialogue):
            name = line.speaker or DEFAULT_SPEAKER
            samples = speakers.setdefault(name, [])
            if len(samples) < 4 and line.text:
                samples.append(line.text)
    return speakers


def _build_user_prompt(*, drama: Drama, episodes: list[Episode], speakers: dict[str, list[str]]) -> str:
    parts = [
        f"剧名：{drama.title}",
        f"题材：{drama.genre}",
        f"整体风格：{drama.style or '未指定'}",
        f"故事梗概：{drama.logline or '未提供'}",
    ]

    if episodes:
        parts.append("\n各集大纲：")
        for episode in episodes[:12]:
            synopsis = (episode.synopsis or "").strip().replace("\n", " ")
            parts.append(f"  第 {episode.number} 集《{episode.title or '未命名'}》：{synopsis[:160]}")

    parts.append("\n剧本里出现过的说话人（必须全部覆盖，不要新增）：")
    for name, samples in speakers.items():
        joined = " / ".join(sample[:40] for sample in samples)
        parts.append(f"  {name}：{joined}")

    return "\n".join(parts)


async def analyze_cast(db: Session, *, drama: Drama, provider: LLMProvider) -> list[Character]:
    """分析角色并写库，返回这部剧的角色列表（幂等：可以反复调用）。"""
    speakers = collect_speakers(db, drama)
    if not speakers:
        raise AppError(
            "这部剧还没有带台词的镜头，请先生成分镜",
            code="no_dialogue",
            status_code=400,
        )

    episodes = list(
        db.scalars(select(Episode).where(Episode.drama_id == drama.id).order_by(Episode.number))
    )
    raw = await provider.complete(
        system=CAST_SYSTEM_PROMPT,
        user=_build_user_prompt(drama=drama, episodes=episodes, speakers=speakers),
    )
    data = extract_json(raw)
    items = data.get("characters")
    if not isinstance(items, list):
        raise AppError("模型返回的角色表格式不对", code="llm_bad_output", status_code=502)

    existing = {
        character.name: character
        for character in db.scalars(select(Character).where(Character.drama_id == drama.id))
    }

    for item in items:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name", "")).strip()
        if not name:
            continue

        gender = normalize_gender(str(item.get("gender", "")).strip().lower())
        age_group = normalize_age_group(str(item.get("age_group", "")).strip().lower())
        profile = NARRATOR_PROFILE if is_narrator(name) else profile_for(gender, age_group)

        character = existing.get(name)
        if character is None:
            character = Character(drama_id=drama.id, name=name)
            db.add(character)
            existing[name] = character

        # 只在"性别/年龄判断变了"或"还没分配过音色"时才覆盖配音参数 ——
        # 这样用户手动改过的音色不会被一次重新分析悄悄冲掉。
        changed = character.gender != gender or character.age_group != age_group
        character.gender = gender
        character.age_group = age_group
        character.personality = str(item.get("personality", "")).strip()[:500]
        if not character.voice or changed:
            character.voice = profile.voice
            character.pitch_shift = profile.pitch_shift
            character.speed = profile.speed

    # 兜底：模型漏掉的说话人也要有档案，否则配音时会掉回默认音色（而且用户看不见、改不了）
    for name in speakers:
        if name in existing:
            continue
        profile = NARRATOR_PROFILE if is_narrator(name) else profile_for(None, None)
        db.add(
            Character(
                drama_id=drama.id,
                name=name,
                gender="unknown",
                age_group="young",
                voice=profile.voice,
                pitch_shift=profile.pitch_shift,
                speed=profile.speed,
                personality="（模型未分析出，使用了默认配音）",
            )
        )

    db.commit()

    characters = list(
        db.scalars(select(Character).where(Character.drama_id == drama.id).order_by(Character.id))
    )
    logger.info("剧 %s 角色表分析完成：%s 个角色（台词里出现 %s 个说话人）", drama.id, len(characters), len(speakers))
    return characters
