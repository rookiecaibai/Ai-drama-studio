"""为某一集的每个镜头生成画面。

两个关键设计：
1. **单张失败不影响其它镜头**：某个镜头生成失败只记在它自己的 image_error 上，
   其它镜头继续。批量任务里"一个坏点毁掉整批"是很糟的体验。
2. **幂等**：文件路径由镜头 id 决定，重新生成直接覆盖同一个文件，
   不会留下孤儿文件；only_missing=True 时还会跳过已经生成过的。
"""

import logging
from collections.abc import Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.db.models import Episode, Shot
from app.services import media
from app.services.images.base import ImageProvider

logger = logging.getLogger(__name__)


async def generate_images_for_episode(
    db: Session,
    *,
    episode: Episode,
    provider: ImageProvider,
    only_missing: bool = True,
    on_total: Callable[[int], None] | None = None,
    on_progress: Callable[[], None] | None = None,
) -> tuple[int, int]:
    """返回 (计划生成数, 成功数)。"""
    shots = list(db.scalars(select(Shot).where(Shot.episode_id == episode.id).order_by(Shot.number)))
    if not shots:
        raise AppError("这一集还没有分镜，请先生成分镜", code="no_shots", status_code=400)

    targets = [s for s in shots if not (only_missing and s.image_path)]
    if on_total is not None:
        on_total(len(targets))

    succeeded = 0
    for shot in targets:
        # 优先用模型给的英文文生图提示词；没有就退回中文画面描述
        prompt = (shot.image_prompt or "").strip() or (shot.description or "").strip()
        if not prompt:
            shot.image_error = "这个镜头既没有文生图提示词、也没有画面描述，无法生成"
            db.commit()
            if on_progress is not None:
                on_progress()
            continue

        try:
            data = await provider.generate(prompt=prompt)
        except Exception as exc:  # noqa: BLE001 —— 单张失败要兜住，不能中断整批
            shot.image_error = str(exc)[:300]
            logger.warning("镜头 %s 画面生成失败: %s", shot.id, exc)
        else:
            rel_path = media.shot_image_rel_path(shot.id)
            media.save_bytes(rel_path, data)
            shot.image_path = rel_path
            shot.image_error = None
            succeeded += 1

        db.commit()
        if on_progress is not None:
            on_progress()

    logger.info("剧集 %s 画面生成结束：计划 %s 张，成功 %s 张", episode.id, len(targets), succeeded)
    return len(targets), succeeded
