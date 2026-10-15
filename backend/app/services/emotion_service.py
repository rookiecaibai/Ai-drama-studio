"""用大模型给每一句台词判断「情绪」和「语速」。

**为什么不能只靠模型自动发挥**
GLM-TTS 会根据台词内容自己判断情绪，但它看到的是**孤立的这一句**。
"你终于来了"这句话：
    在重逢戏里是温柔，在报仇戏里是压抑的愤怒，在鬼片里是惊恐。
同一句话，语境不同，配法完全不同。所以我们要把**上下文**喂给模型，
让它先当一次"配音导演"，逐句标出情绪、强度、语速，再把语速交给 TTS。

**为什么不用"在文本里写情感标签"这种更简单的办法**
实测过，不行：把 `急迫：xxx` 送进 TTS，模型会把"急迫"两个字**念出来**。
（用 ASR 把生成的音频转写回来验证过：转写结果里真的出现了"急迫"。）
所以情绪只能通过**音色、变调、语速**这些参数来体现，不能靠文本提示。
"""

import logging
import re

from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.db.models import Character, DialogueLine, Drama, Episode, Shot
from app.services.llm.base import LLMProvider
from app.services.llm.parsing import extract_json

logger = logging.getLogger(__name__)

#: 允许的情绪词。给模型一个闭集，避免它每次造新词导致前端展示和统计不可控。
EMOTIONS = (
    "平静", "温柔", "暧昧", "挑逗", "羞涩", "撒娇",
    "紧张", "急迫", "惊恐", "愤怒", "压抑", "冷笑", "嘲讽",
    "悲伤", "绝望", "疲惫", "虚弱", "坚定", "惊喜", "疑惑", "犹豫", "命令",
)

#: 语速基调 -> 系数
_PACE_SPEED = {"slow": 0.88, "normal": 1.0, "fast": 1.12}

#: 情绪 -> 语速的额外修正（在基调之上再乘）
_EMOTION_SPEED = {
    "急迫": 1.06, "惊恐": 1.08, "愤怒": 1.05, "命令": 1.03, "紧张": 1.04, "惊喜": 1.05,
    "冷笑": 0.97, "嘲讽": 0.97, "坚定": 0.98, "疑惑": 1.0, "犹豫": 0.95,
    "温柔": 0.96, "暧昧": 0.93, "挑逗": 0.94, "羞涩": 0.94, "撒娇": 0.97,
    "悲伤": 0.94, "绝望": 0.92, "疲惫": 0.93, "虚弱": 0.91, "压抑": 0.96,
    "平静": 1.0,
}

#: 语速的最终夹取范围（智谱 GLM-TTS 的 speed 取值范围是 [0.5, 2]，留点余量）
MIN_SPEED, MAX_SPEED = 0.6, 1.8

EMOTION_SYSTEM_PROMPT = """你是短剧的配音导演。请通读这一集的剧本，给**每一句台词**判断说话时的情绪和语速。

**关键：情绪要结合当时的处境判断，不能只看这句话本身。** 例如：
- 被人跟踪时的一句"谁在那里？" -> 紧张、试探，而不是平静的疑问
- 两人独处、气氛暧昧时的一句"你干嘛看着我" -> 娇嗔、暧昧
- 面对仇人时的一句"你终于来了" -> 压抑的愤怒
- 凌晨三点发现监控里的人是自己 -> 惊恐

情绪用词请**尽量从这个列表里选**（便于统一处理）：
平静 / 温柔 / 暧昧 / 挑逗 / 羞涩 / 撒娇 / 紧张 / 急迫 / 惊恐 / 愤怒 / 压抑 /
冷笑 / 嘲讽 / 悲伤 / 绝望 / 疲惫 / 虚弱 / 坚定 / 惊喜 / 疑惑 / 犹豫 / 命令

同时给出：
- intensity：情绪强度，0.0 ~ 1.0（0.5 表示一般）
- pace：语速，slow（慢）/ normal（正常）/ fast（快）

只输出 JSON，不要解释：
{"lines": [{"shot": 1, "line": 0, "speaker": "林晚", "emotion": "惊恐",
  "intensity": 0.85, "pace": "fast"}]}

要求：
1. `shot` 是镜头序号（从 1 开始），`line` 是该镜头里第几句台词（从 0 开始）。
   **必须和输入里的编号严格对应，一句都不能漏。**
2. **speaker 是关键字段**：输入里如果有句子被标成「待推断说话人」，
   你必须结合剧情、前面的对话、角色性格判断它最可能是谁在说，
   把真实角色名填进 speaker；只有确定是旁白/画面描述时才写「旁白」。
3. 旁白一般是平静或低沉，除非情节本身很紧张。
4. 同一句话在不同处境下配法不同，请按剧情给，不要一律"平静"。""".strip()


def speed_for(base_speed: float, *, emotion: str, pace: str, intensity: float) -> float:
    """把「角色基础语速 + 情绪 + 语速基调 + 强度」合成最终语速。

    设计要点：**强度只放大"偏离 1.0 的部分"**。
    强度 0.5（一般）时几乎不改变语速；强度拉满才明显加快或放慢。
    否则只要标了情绪就一律加速，整集听起来会很赶、很假。
    """
    factor = _PACE_SPEED.get(pace, 1.0) * _EMOTION_SPEED.get(emotion, 1.0)
    strength = 0.6 + 0.8 * max(0.0, min(1.0, intensity))  # 0.6 ~ 1.4
    factor = 1.0 + (factor - 1.0) * strength
    return round(max(MIN_SPEED, min(MAX_SPEED, base_speed * factor)), 3)


def _character_hint(characters: list[Character]) -> str:
    if not characters:
        return ""
    lines = ["\n角色说明（判断语气时参考）："]
    for character in characters:
        if character.personality:
            lines.append(f"  {character.name}：{character.personality}")
    return "\n".join(lines) if len(lines) > 1 else ""


def _build_user_prompt(
    *,
    drama: Drama,
    episode: Episode,
    entries: list[tuple[Shot, list[DialogueLine]]],
    characters: list[Character],
) -> str:
    parts = [
        f"剧名：{drama.title}（{drama.genre}）",
        f"故事梗概：{drama.logline or '未提供'}",
        f"本集：第 {episode.number} 集《{episode.title or '未命名'}》",
        f"本集大纲：{episode.synopsis or '未提供'}",
        _character_hint(characters),
        "\n以下是本集所有台词，请逐句判断情绪：",
    ]
    for shot, lines in entries:
        context = (shot.description or "").strip().replace("\n", " ")
        parts.append(f"\n镜头 {shot.number}（{shot.shot_type}｜{context[:70]}）")
        for index, line in enumerate(lines):
            speaker_label = line.speaker if line.speaker else "（待推断说话人）"
            cue_label = f" 提示[{line.cue}]" if line.cue else ""
            parts.append(f"  line {index}｜{speaker_label}{cue_label}｜{line.text}")
    return "\n".join(parts)


async def analyze_emotions(
    db: Session,
    *,
    drama: Drama,
    episode: Episode,
    entries: list[tuple[Shot, list[DialogueLine]]],
    characters: list[Character],
    provider: LLMProvider,
) -> int:
    """给每一句台词补上情绪/强度/语速。返回被更新的句数。

    模型漏报或编号对不上时不会炸：没匹配上的句子保留默认值（平静、语速 1.0）。
    —— 配音是"能跑完"比"每一句都完美"更重要的事，不能让一次坏返回毁掉整个任务。
    """
    if not any(lines for _, lines in entries):
        return 0

    raw = await provider.complete(
        system=EMOTION_SYSTEM_PROMPT,
        user=_build_user_prompt(drama=drama, episode=episode, entries=entries, characters=characters),
    )
    data = extract_json(raw)
    items = data.get("lines")
    if not isinstance(items, list):
        raise AppError("模型返回的情绪分析格式不对", code="llm_bad_output", status_code=502)

    # 建立 (镜头序号, 句序号) -> DialogueLine 的索引
    index: dict[tuple[int, int], DialogueLine] = {}
    for shot, lines in entries:
        for position, line in enumerate(lines):
            index[(shot.number, position)] = line

    # 基础语速来自**角色**，不是来自 line.speed。
    # 这一点很关键：line.speed 存的是"基础 × 情绪"的结果，
    # 如果拿它再乘一次，重复分析两次语速就会越乘越离谱（0.93 × 0.93 × …）。
    base_speed_by_character = {character.id: character.speed for character in characters}

    updated = 0
    for item in items:
        if not isinstance(item, dict):
            continue
        try:
            key = (int(item.get("shot")), int(item.get("line")))
        except (TypeError, ValueError):
            continue
        line = index.get(key)
        if line is None:
            continue

        emotion = str(item.get("emotion", "")).strip()
        if emotion not in EMOTIONS:
            # 模型造了新词也不丢弃：保留原词用于展示，但语速修正按默认处理
            emotion = emotion[:20] or "平静"

        try:
            intensity = float(item.get("intensity", 0.5))
        except (TypeError, ValueError):
            intensity = 0.5
        intensity = max(0.0, min(1.0, intensity))

        pace = str(item.get("pace", "normal")).strip().lower()
        if pace not in _PACE_SPEED:
            pace = "normal"

        base_speed = base_speed_by_character.get(line.character_id or -1, 1.0)

        # 补全说话人：裸台词（line.speaker 为空）的说话人由模型结合上下文推断。
        # 这是"男主台词被配成女声"的修复关键 —— 不能默认塞给旁白。
        inferred = re.sub(r"[（(【\[].*$", "", str(item.get("speaker", "")).strip()).strip()[:60]
        if inferred in ("待推断说话人", "未知", "无", "旁白或画外音"):
            inferred = ""
        if inferred and not line.speaker:
            line.speaker = inferred

        line.emotion = emotion
        line.intensity = intensity
        line.speed = speed_for(base_speed, emotion=emotion, pace=pace, intensity=intensity)
        updated += 1

    db.commit()
    logger.info("剧集 %s 情绪分析完成：%s/%s 句有结果", episode.id, updated, len(index))
    return updated
