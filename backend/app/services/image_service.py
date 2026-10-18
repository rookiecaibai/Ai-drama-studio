"""为某一集的每个镜头生成画面。

三个关键设计：

1. **单张失败不影响其它镜头**：某个镜头生成失败只记在它自己的 image_error 上，
   其它镜头继续。批量任务里"一个坏点毁掉整批"是很糟的体验。
2. **幂等**：文件路径由镜头 id 决定，重新生成直接覆盖同一个文件，
   不会留下孤儿文件；only_missing=True 时还会跳过已经生成过的。
3. **画谁就带上谁的外貌**：先算出这个镜头里出场了哪些角色（台词 + 名字出现在描述里），
   把他们的 look_prompt 拼到提示词**前面**，再跟上镜头自己的场景描述。
   这是"同一个角色在不同镜头里长得像"的实现方式（模型没有记忆，只能靠文字锚定）。
"""

import logging
from collections import defaultdict
from collections.abc import Callable
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.db.models import Character, DialogueLine, Episode, Shot
from app.services import media
from app.services.character_look import characters_in_shot, compose_shot_prompt
from app.services.images.base import ImageProvider

logger = logging.getLogger(__name__)


def _load_lines_by_shot(db: Session, shot_ids: list[int]) -> dict[int, list[DialogueLine]]:
    """显式查这个镜头的台词行。

    为什么不直接读 shot.lines：ORM 的关系集合**一旦被加载过就不会刷新**，
    如果别处先加载了空集合，这里读到的就是空列表 —— 表现是"角色一律识别不出来"，
    而且完全不报错。这个坑我们踩过（见 docs/04 踩坑合集），所以这里显式查询。
    """
    if not shot_ids:
        return {}
    rows = db.scalars(select(DialogueLine).where(DialogueLine.shot_id.in_(shot_ids))).all()
    grouped: dict[int, list[DialogueLine]] = defaultdict(list)
    for line in rows:
        grouped[line.shot_id].append(line)
    return grouped


def _pick_reference(shot_characters: list[Character]) -> Path | None:
    """哪张三视图可以当参考图传给模型。

    只有当**这个镜头恰好只有一个角色**、且这个角色已经有了三视图时才传。
    为什么这么保守：一张参考图对应一个具体的长相，如果镜头里有两个人，
    传其中一个人的设定图反而会把另一个人的长相带偏 —— 不如老老实实走纯文本。
    （也只有 supports_reference=True 的 Provider 才会走到这里。）
    """
    usable = [c for c in shot_characters if c.turnaround_path]
    if len(shot_characters) == 1 and len(usable) == 1:
        rel_path = usable[0].turnaround_path or ""
        if media.exists(rel_path):
            return media.absolute_path(rel_path)
    return None


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

    # 这部剧的角色表（可能为空 —— 用户还没做配音/分析过角色，那就退化成纯场景描述）
    characters = list(
        db.scalars(
            select(Character).where(Character.drama_id == episode.drama_id).order_by(Character.id)
        )
    )
    lines_by_shot = _load_lines_by_shot(db, [s.id for s in targets])
    style = episode.drama.style if episode.drama else ""

    if not characters:
        logger.info(
            "剧 %s 还没有角色表，镜头画面将只按场景描述生成（角色一致性无法保证）",
            episode.drama_id,
        )
    elif not provider.supports_reference:
        logger.info(
            "当前图片模型不支持参考图：角色一致性靠 look_prompt 文本锚定（%s 个角色）",
            len(characters),
        )

    succeeded = 0
    for shot in targets:
        present = characters_in_shot(shot, characters, lines_by_shot.get(shot.id, []))
        prompt, used_names = compose_shot_prompt(shot, present, style=style)
        if not prompt:
            shot.image_error = "这个镜头既没有文生图提示词、也没有画面描述，无法生成"
            db.commit()
            if on_progress is not None:
                on_progress()
            continue

        reference = _pick_reference(present) if provider.supports_reference else None
        if used_names:
            logger.info(
                "镜头 %s 画面：出场角色 %s%s",
                shot.id,
                "、".join(used_names),
                "（附参考图）" if reference else "",
            )

        previous = shot.image_path
        try:
            data = await provider.generate(prompt=prompt, reference_image=reference)
        except Exception as exc:  # noqa: BLE001 —— 单张失败要兜住，不能中断整批
            shot.image_error = str(exc)[:300]
            logger.warning("镜头 %s 画面生成失败: %s", shot.id, exc)
        else:
            # 按**文件头**决定扩展名，而不是假设厂商返回 PNG
            # （智谱实际返回 JPEG —— 见 media.detect_image_extension 的说明）
            rel_path = media.shot_image_rel_path(shot.id, media.detect_image_extension(data))
            media.save_bytes(rel_path, data)
            if previous and previous != rel_path:
                media.delete(previous)  # 换厂商导致格式变了，删掉旧文件
            shot.image_path = rel_path
            shot.image_error = None
            succeeded += 1

        db.commit()
        if on_progress is not None:
            on_progress()

    logger.info("剧集 %s 画面生成结束：计划 %s 张，成功 %s 张", episode.id, len(targets), succeeded)
    return len(targets), succeeded
