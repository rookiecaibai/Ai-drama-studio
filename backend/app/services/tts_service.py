"""为某一集的镜头批量生成配音。

三个值得注意的设计：
1. **只给有台词的镜头配音**：纯画面镜头没有话可说，给它生成音频既没意义又白花钱。
   所以"有台词"的镜头才算进 total，前端也能看到"这一集有几句台词"。
2. **换厂商会换音频格式**（mock 出 wav、真实厂商常出 mp3），落盘路径会变，
   这时要顺手删掉旧扩展名的文件，否则就是孤儿文件。
3. **单段失败不影响其它**：失败原因记在镜头自己的 audio_error 上。
"""

import logging
from collections.abc import Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.db.models import Episode, Shot
from app.services import media
from app.services.tts.base import TtsProvider

logger = logging.getLogger(__name__)


async def generate_audio_for_episode(
    db: Session,
    *,
    episode: Episode,
    provider: TtsProvider,
    only_missing: bool = True,
    on_total: Callable[[int], None] | None = None,
    on_progress: Callable[[], None] | None = None,
) -> tuple[int, int]:
    """返回 (计划配音数, 成功数)。"""
    shots = list(db.scalars(select(Shot).where(Shot.episode_id == episode.id).order_by(Shot.number)))
    if not shots:
        raise AppError("这一集还没有分镜，请先生成分镜", code="no_shots", status_code=400)

    with_lines = [s for s in shots if (s.dialogue or "").strip()]
    targets = [s for s in with_lines if not (only_missing and s.audio_path)]
    if on_total is not None:
        on_total(len(targets))

    succeeded = 0
    for shot in targets:
        text = shot.dialogue.strip()
        rel_path = media.shot_audio_rel_path(shot.id, provider.extension)
        previous = shot.audio_path

        try:
            data = await provider.synthesize(text=text, style=shot.audio_prompt or "")
            if not data:
                raise AppError("配音服务返回了空音频", code="tts_bad_output", status_code=502)
        except Exception as exc:  # noqa: BLE001 —— 单段失败要兜住，不能中断整批
            shot.audio_error = str(exc)[:300]
            logger.warning("镜头 %s 配音失败: %s", shot.id, exc)
        else:
            media.save_bytes(rel_path, data)
            if previous and previous != rel_path:
                media.delete(previous)  # 扩展名变了，删掉旧文件
            shot.audio_path = rel_path
            shot.audio_error = None
            succeeded += 1

        db.commit()
        if on_progress is not None:
            on_progress()

    logger.info(
        "剧集 %s 配音结束：有台词 %s 段，计划处理 %s 段，成功 %s 段",
        episode.id,
        len(with_lines),
        len(targets),
        succeeded,
    )
    return len(targets), succeeded
