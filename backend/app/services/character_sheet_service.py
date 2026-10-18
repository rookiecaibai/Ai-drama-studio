"""批量生成角色三视图（设定图）。

它在流水线里的位置：

    角色卡（性别/年龄/外貌/职业）→ 【三视图】 → 每个镜头的画面
                                      ↑              ↑
                                      └── look_prompt（文本锚点）──┘

**三视图到底解决了什么、没解决什么**（这点必须说清，否则会误以为它是万能的）：

✅ 解决了：用户第一次能"看见"这个角色长什么样，可以在画镜头之前就反复调整
   （改外貌描述 → 重生成三视图 → 满意了再去画镜头）。
   在这个意义上，它是**角色设计的确认环节**，能省掉大量"画完全片才发现角色不对"的返工。

✅ 解决了：三视图生成后，角色的外貌被固定成了一段文字（look_prompt），
   每个镜头画这个角色时都会带上它 —— 这就是我们能做到的角色一致性。

❌ 没解决：**模型并没有真的"看着三视图"去画镜头**。智谱的 cogview-3-flash /
   cogview-4 / glm-image 都是纯文本输入，不接受参考图。
   所以"同一张脸"的一致性来自**文字描述相同**，而不是图像特征相同 ——
   当外貌描述本身很模糊（比如只写"普通青年"）时，长相仍然会飘。
   想真正锁住长相，必须换一个支持参考图/角色参考的厂商（接口已经留好了：
   ImageProvider.supports_reference）。

这段说明我特意写进代码里，因为它直接决定了用户该期待什么。
"""

import logging
from collections.abc import Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.db.models import Character, Drama
from app.services import media
from app.services.character_look import build_sheet_prompt
from app.services.images.base import ImageProvider
from app.services.voice_profile import is_narrator

logger = logging.getLogger(__name__)


async def generate_turnarounds_for_drama(
    db: Session,
    *,
    drama: Drama,
    provider: ImageProvider,
    only_missing: bool = True,
    on_total: Callable[[int], None] | None = None,
    on_progress: Callable[[], None] | None = None,
) -> tuple[int, int]:
    """为一部剧的所有角色生成三视图。返回 (计划生成数, 成功数)。"""
    characters = list(
        db.scalars(select(Character).where(Character.drama_id == drama.id).order_by(Character.id))
    )
    if not characters:
        raise AppError(
            "这部剧还没有角色表，请先生成分镜并做一次配音（会自动分析角色表），"
            "或手动创建角色",
            code="no_characters",
            status_code=400,
        )

    # **旁白不算角色**：它没有形象，画一张"旁白的三视图"纯属浪费钱，
    # 还会在角色卡列表里留一个永远用不上的空壳。
    visual = [c for c in characters if not is_narrator(c.name)]
    skipped = len(characters) - len(visual)
    if skipped:
        logger.info("剧 %s 跳过 %s 个旁白类角色（无形象，不需要三视图）", drama.id, skipped)

    if not visual:
        raise AppError(
            "这部剧只有旁白类角色，没有需要三视图的可见角色",
            code="no_visual_characters",
            status_code=400,
        )

    targets = [c for c in visual if not (only_missing and c.turnaround_path)]
    if on_total is not None:
        on_total(len(targets))

    succeeded = 0
    for character in targets:
        prompt = build_sheet_prompt(character, style=drama.style or "")
        previous = character.turnaround_path
        try:
            # 注意：这里**不传参考图** —— 三视图是"从文字生成"的源头，
            # 它自己不能依赖参考图，否则就成了循环依赖。
            data = await provider.generate(prompt=prompt)
            if not data:
                raise AppError("图片模型返回了空数据", code="image_bad_output", status_code=502)
        except Exception as exc:  # noqa: BLE001 —— 单个角色失败不能中断整批
            character.turnaround_error = str(exc)[:300]
            logger.warning("角色 %s 三视图生成失败: %s", character.id, exc)
        else:
            # 按文件头决定扩展名，不假设厂商返回 PNG（智谱实际返回 JPEG）
            rel_path = media.character_sheet_rel_path(
                character.id, media.detect_image_extension(data)
            )
            media.save_bytes(rel_path, data)
            if previous and previous != rel_path:
                media.delete(previous)  # 格式变了，删掉旧文件，不留孤儿
            character.turnaround_path = rel_path
            character.turnaround_error = None
            succeeded += 1

        db.commit()
        if on_progress is not None:
            on_progress()

    logger.info(
        "剧 %s 三视图结束：角色 %s 个（跳过旁白 %s 个），计划 %s 个，成功 %s 个",
        drama.id,
        len(visual),
        skipped,
        len(targets),
        succeeded,
    )
    return len(targets), succeeded
