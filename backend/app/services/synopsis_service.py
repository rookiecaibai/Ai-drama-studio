"""生成"本集大纲"的业务逻辑。

和生成分镜的区别：
- 分镜：一次性生成很多结构化内容，用异步任务（耗时长）；
- 大纲：就一段文字，几秒钟，直接同步返回即可 —— 同步/异步的选择应该看耗时，
  而不是一律上异步。

关键设计：**生成但不落库**。
AI 只给建议，用户看过、改过、确认后才保存。原因：
1. 大模型输出是概率性的，直接覆盖用户写好的内容是产品灾难；
2. 用户往往需要"AI 起草 → 自己润色 → 再生成分镜"这个流程。
"""

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.db.models import Drama, Episode
from app.services.llm.base import LLMProvider
from app.services.llm.parsing import extract_json

logger = logging.getLogger(__name__)

SYNOPSIS_SYSTEM_PROMPT = """你是一位资深竖屏短剧编剧，现在只做一件事：写"本集内容大纲"。
你必须严格只输出一个 JSON 对象，不要任何解释、寒暄或 Markdown 代码块。
结构如下：
{"synopsis": "本集大纲"}

写作要求：
1. 长度 80-150 字，写清这一集发生了什么：谁、在哪、做了什么、结尾留下什么悬念；
2. 如果给了前几集内容，必须自然衔接，不要重复已经发生过的情节，也不要提前消耗后面的高潮；
3. 如果没有前几集（第一集），要负责交代主要人物、并抛出贯穿全剧的主线钩子；
4. 符合短剧节奏：开场 3 秒内要有冲突或异常，结尾必须留钩子。""".strip()


def _load_context_episodes(db: Session, drama: Drama, after_number: int | None) -> list[Episode]:
    """取前情上下文：编号小于 after_number 的剧集。

    after_number 为 None 时表示"接在现有所有剧集之后"（新建一集的情形）。
    """
    stmt = select(Episode).where(Episode.drama_id == drama.id)
    if after_number is not None:
        stmt = stmt.where(Episode.number < after_number)
    return list(db.scalars(stmt.order_by(Episode.number)))


def _build_user_prompt(
    *,
    drama: Drama,
    episode_number: int,
    title: str,
    context: list[Episode],
) -> str:
    lines = [
        f"剧名：{drama.title}",
        f"题材：{drama.genre}",
        f"整体风格：{drama.style or '未指定'}",
        f"全剧梗概：{drama.logline or '未指定'}",
        f"本集序号：第 {episode_number} 集",
        f"本集标题：{title or '（未命名，请你不要起名，只写大纲）'}",
    ]
    if context:
        lines.append("前几集已经发生的内容（务必衔接，不要重复）：")
        for ep in context:
            summary = ep.synopsis.strip() or ep.title or "（这一集还没有内容）"
            lines.append(f"  第 {ep.number} 集《{ep.title or '未命名'}》：{summary}")
    else:
        lines.append("这是第一集，没有前情。")
    return "\n".join(lines)


async def generate_synopsis(
    db: Session,
    *,
    drama: Drama,
    title: str,
    after_number: int | None,
    provider: LLMProvider,
) -> tuple[str, list[int]]:
    """返回 (大纲文本, 参考过的剧集编号列表)。不写数据库。"""
    context = _load_context_episodes(db, drama, after_number)

    # 目标集号：显式指定就用它，否则接在现有剧集之后
    if after_number is not None:
        episode_number = after_number
    else:
        episode_number = (context[-1].number + 1) if context else 1

    user_prompt = _build_user_prompt(
        drama=drama,
        episode_number=episode_number,
        title=title,
        context=context,
    )

    raw = await provider.complete(system=SYNOPSIS_SYSTEM_PROMPT, user=user_prompt)
    data = extract_json(raw)

    synopsis = str(data.get("synopsis") or "").strip()
    if not synopsis:
        raise AppError("大模型没有返回大纲内容", code="llm_bad_output", status_code=502)

    logger.info("剧集大纲生成完成：短剧 %s 第 %s 集，参考了 %s 集前情", drama.id, episode_number, len(context))
    return synopsis, [ep.number for ep in context]
