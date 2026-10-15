"""逐镜头生成动态片段（图生视频）。

**它在流水线里的位置**
    分镜 → 画面(图) → 【动态片段(视频)】 → 配音 → 合成成片
动态片段是可选的：没有它就退回"静图定格 + 配音"。
所以这一步的存在与否**不会让成片功能不可用**，只会影响画面动不动。

**两个刻意的设计**
1. **单镜头失败不影响其它镜头**：失败原因记在这个镜头自己的 clip_error 上，
   其它镜头继续。视频模型最容易限流/超时，一个坏点毁掉整集是最糟的体验。
2. **提示词和文生图不一样**：文生图的 image_prompt 描述"这一帧长什么样"（静），
   图生视频要的是"接下来怎么动"（动）。所以这里用 **运镜(camera) + 画面描述** 组合，
   而不是直接复用 image_prompt。
"""

import logging
from collections.abc import Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.db.models import Episode, Shot
from app.services import media
from app.services.videos.base import VideoClipProvider

logger = logging.getLogger(__name__)

#: 兜底的运镜提示词（镜头既没写运镜也没写描述时用）
DEFAULT_MOTION_PROMPT = "镜头缓慢推进，画面自然流动，光影有细微变化"


def motion_prompt(shot: Shot) -> str:
    """给图生视频模型写"运动提示词"。

    后续可以做得更讲究：让大模型读这一集的剧情，给每个镜头写具体的动作
    （"男主缓缓抬头看向镜头，雨水从发梢滴落"），比机械拼接运镜和描述更贴切。
    """
    parts = [part.strip() for part in (shot.camera, shot.description) if part and part.strip()]
    text = "，".join(parts)
    return text[:400] or DEFAULT_MOTION_PROMPT


async def generate_clips_for_episode(
    db: Session,
    *,
    episode: Episode,
    provider: VideoClipProvider,
    only_missing: bool = True,
    on_total: Callable[[int], None] | None = None,
    on_progress: Callable[[], None] | None = None,
) -> tuple[int, int]:
    """返回 (计划生成数, 成功数)。"""
    shots = list(db.scalars(select(Shot).where(Shot.episode_id == episode.id).order_by(Shot.number)))
    if not shots:
        raise AppError("这一集还没有分镜，请先生成分镜", code="no_shots", status_code=400)

    with_image = [shot for shot in shots if shot.image_path and media.exists(shot.image_path)]
    if not with_image:
        raise AppError(
            "这一集还没有画面，请先生成画面再生成动态镜头",
            code="images_missing",
            status_code=400,
        )

    targets = [shot for shot in with_image if not (only_missing and shot.clip_path)]
    if on_total is not None:
        on_total(len(targets))

    succeeded = 0
    for shot in targets:
        try:
            data = await provider.generate(
                image_path=media.absolute_path(shot.image_path or ""),
                prompt=motion_prompt(shot),
            )
            if not data:
                raise AppError("图生视频返回了空视频", code="video_bad_output", status_code=502)
        except Exception as exc:  # noqa: BLE001 —— 单镜头失败不能中断整批
            shot.clip_error = str(exc)[:300]
            logger.warning("镜头 %s 动态片段生成失败: %s", shot.id, exc)
        else:
            rel_path = media.shot_clip_rel_path(shot.id)
            media.save_bytes(rel_path, data)
            shot.clip_path = rel_path
            shot.clip_error = None
            succeeded += 1

        db.commit()
        if on_progress is not None:
            on_progress()

    logger.info(
        "剧集 %s 动态片段结束：有画面的镜头 %s 个，计划处理 %s 个，成功 %s 个",
        episode.id,
        len(with_image),
        len(targets),
        succeeded,
    )
    return len(targets), succeeded
