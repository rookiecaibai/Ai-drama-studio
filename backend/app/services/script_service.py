"""剧本生成业务逻辑。

这一层（Service）存在的意义，是回答"接口层该多薄"这个问题：
- 路由函数只负责：收参数、调 service、返回结果（10 行以内）；
- 提示词怎么拼、模型返回的脏数据怎么清洗、数据库怎么落库，全在这里。
这样同一套逻辑可以被 HTTP 接口、定时任务、CLI 脚本复用。
"""

import json
import logging
import re

from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.db.models import Drama, Episode, Shot
from app.services.llm.base import LLMProvider

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """你是一位资深竖屏短剧编剧兼分镜师，擅长写 60-90 秒一集的高信息密度短剧。
你必须严格只输出一个 JSON 对象，不要输出任何解释、寒暄或 Markdown 代码块。
JSON 结构如下：
{
  "title": "本集标题",
  "synopsis": "本集梗概，80字以内",
  "shots": [
    {
      "shot_type": "景别：特写/近景/中景/全景/远景",
      "camera": "运镜：固定/推镜/拉镜/跟拍/手持微晃/正反打",
      "description": "画面描述，具体到人物动作、环境、光线",
      "dialogue": "台词或旁白，没有就留空字符串",
      "duration_sec": 3.5,
      "image_prompt": "英文文生图提示词，描述画面风格与构图"
    }
  ]
}
所有镜头时长之和应接近 60-90 秒。""".strip()


def _build_user_prompt(*, drama: Drama, episode: Episode, shot_count: int, extra_hint: str) -> str:
    lines = [
        f"剧名：{drama.title}",
        f"题材：{drama.genre}",
        f"整体风格：{drama.style or '未指定'}",
        f"故事梗概：{drama.logline or '未指定'}",
        f"本集序号：第 {episode.number} 集",
        f"本集标题：{episode.title or '（未命名，请你起一个）'}",
        f"本集大纲：{episode.synopsis or '（未提供，请你基于整体梗概合理设计）'}",
        f"请生成 {shot_count} 个镜头。",
    ]
    if extra_hint:
        lines.append(f"额外要求：{extra_hint}")
    return "\n".join(lines)


def _extract_json(text: str) -> dict:
    """从模型输出里抠出 JSON。

    现实情况：即使要求"只输出 JSON"，模型仍可能包一层 ```json``` 或加一句开场白，
    所以必须有容错解析。这是 AI 应用开发和传统后端最大的差异之一：
    上游返回是"概率性"的，代码必须假设它会不规范。
    """
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    raw = fenced.group(1) if fenced else text

    start, end = raw.find("{"), raw.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise AppError("大模型没有返回可解析的 JSON", code="llm_bad_output", status_code=502)

    try:
        data = json.loads(raw[start : end + 1])
    except json.JSONDecodeError as exc:
        raise AppError(f"大模型返回的 JSON 解析失败: {exc}", code="llm_bad_output", status_code=502) from exc

    if not isinstance(data, dict):
        raise AppError("大模型返回的 JSON 顶层不是对象", code="llm_bad_output", status_code=502)
    return data


def _safe_str(value: object, limit: int | None = None) -> str:
    text = "" if value is None else str(value)
    text = text.strip()
    return text[:limit] if limit else text


def _safe_float(value: object, default: float = 3.0) -> float:
    try:
        num = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
    if num <= 0 or num > 60:
        return default
    return num


async def generate_episode_script(
    db: Session,
    *,
    episode: Episode,
    drama: Drama,
    provider: LLMProvider,
    shot_count: int = 6,
    extra_hint: str = "",
) -> Episode:
    """调用大模型生成本集剧本与分镜，并写库。

    注意：这里是"全量替换"而不是追加，重复点击生成不会产生脏数据（幂等）。
    """
    user_prompt = _build_user_prompt(drama=drama, episode=episode, shot_count=shot_count, extra_hint=extra_hint)
    raw = await provider.complete(system=SYSTEM_PROMPT, user=user_prompt)
    data = _extract_json(raw)

    shots = data.get("shots")
    if not isinstance(shots, list) or not shots:
        raise AppError("大模型返回的 shots 为空", code="llm_bad_output", status_code=502)

    # 1) 先清掉旧镜头，保证重新生成不残留
    db.execute(delete(Shot).where(Shot.episode_id == episode.id))

    # 2) 再写新镜头
    for index, item in enumerate(shots, start=1):
        if not isinstance(item, dict):
            continue
        db.add(
            Shot(
                episode_id=episode.id,
                number=index,
                shot_type=_safe_str(item.get("shot_type") or "中景", 30),
                camera=_safe_str(item.get("camera"), 60),
                description=_safe_str(item.get("description")),
                dialogue=_safe_str(item.get("dialogue")),
                duration_sec=_safe_float(item.get("duration_sec")),
                image_prompt=_safe_str(item.get("image_prompt")),
            )
        )

    # 3) 顺手把标题/梗概/状态更新掉
    episode.title = _safe_str(data.get("title"), 120) or episode.title
    episode.synopsis = _safe_str(data.get("synopsis")) or episode.synopsis
    episode.status = "ready"

    db.commit()
    db.refresh(episode)
    logger.info("剧集 %s 生成完成，镜头数=%s，provider=%s", episode.id, len(shots), provider.name)
    return episode
