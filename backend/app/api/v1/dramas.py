"""短剧与剧集相关接口（都需要登录，且只能操作自己的资源）。"""

from fastapi import APIRouter, Query, status
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from app.api.deps import CurrentUser, DbSession, LlmProvider, QuotaServiceDep
from app.core.errors import ConflictError, NotFoundError
from app.db.models import Drama, Episode, Shot, User
from app.schemas.drama import DramaCreate, DramaDetail, DramaRead, DramaUpdate
from app.schemas.episode import (
    EpisodeCreate,
    EpisodeRead,
    SynopsisGenerateRequest,
    SynopsisRead,
)
from app.services import media
from app.services.quota import QuotaService
from app.services.synopsis_service import generate_synopsis

router = APIRouter(prefix="/dramas", tags=["dramas"])


def _get_owned_drama(db: DbSession, drama_id: int, user: User) -> Drama:
    """取"属于当前用户"的短剧。

    注意一个安全细节：资源不存在和资源不属于你，返回的是同一个 404。
    如果属于别人时返回 403，外人就能靠"404/403 的区别"探测出哪些 id 真实存在。
    """
    drama = db.get(Drama, drama_id)
    if drama is None or drama.owner_id != user.id:
        raise NotFoundError(f"短剧 {drama_id} 不存在")
    return drama


@router.post("", response_model=DramaRead, status_code=status.HTTP_201_CREATED, summary="创建短剧")
def create_drama(payload: DramaCreate, db: DbSession, user: CurrentUser) -> Drama:
    drama = Drama(**payload.model_dump(), owner_id=user.id)
    db.add(drama)
    db.commit()
    db.refresh(drama)  # 拿到数据库生成的 id / 时间戳
    return drama


@router.get("", response_model=list[DramaRead], summary="我的短剧列表")
def list_dramas(
    db: DbSession,
    user: CurrentUser,
    offset: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
) -> list[Drama]:
    # 只返回当前用户的剧 —— 这就是"资源归属"在列表层的体现
    # 分页是硬要求：不加 limit 的列表接口，数据一多就是线上事故（内存打爆 + 慢查询）
    stmt = (
        select(Drama)
        .where(Drama.owner_id == user.id)
        .order_by(Drama.id.desc())
        .offset(offset)
        .limit(limit)
    )
    return list(db.scalars(stmt))


@router.get("/{drama_id}", response_model=DramaDetail, summary="短剧详情（含剧集列表）")
def get_drama(drama_id: int, db: DbSession, user: CurrentUser) -> Drama:
    _get_owned_drama(db, drama_id, user)  # 归属校验
    # selectinload：用一条额外的 IN 查询把剧集一起取回来，避免"N+1 查询"
    stmt = select(Drama).options(selectinload(Drama.episodes)).where(Drama.id == drama_id)
    return db.scalars(stmt).first()  # 上面校验过存在性，这里必然有值


@router.patch("/{drama_id}", response_model=DramaRead, summary="局部更新短剧")
def update_drama(drama_id: int, payload: DramaUpdate, db: DbSession, user: CurrentUser) -> Drama:
    drama = _get_owned_drama(db, drama_id, user)
    # exclude_unset：只更新请求里真正出现过的字段（PATCH 语义）
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(drama, field, value)
    db.commit()
    db.refresh(drama)
    return drama


@router.delete("/{drama_id}", status_code=status.HTTP_204_NO_CONTENT, summary="删除短剧")
def delete_drama(drama_id: int, db: DbSession, user: CurrentUser) -> None:
    drama = _get_owned_drama(db, drama_id, user)

    # 先收集所有已生成媒体（画面 + 配音）的文件路径 ——
    # 数据库的级联删除只删记录，不会删磁盘文件。
    # 不处理的话，每删一部剧就会在磁盘上留下一堆没人认领的孤儿文件。
    media_rows = db.execute(
        select(Shot.image_path, Shot.audio_path)
        .join(Episode, Shot.episode_id == Episode.id)
        .where(Episode.drama_id == drama.id)
    ).all()
    video_paths = list(
        db.scalars(
            select(Episode.video_path).where(
                Episode.drama_id == drama.id, Episode.video_path.is_not(None)
            )
        )
    )

    db.delete(drama)  # cascade 会连带删掉剧集和镜头
    db.commit()

    for image_path, audio_path in media_rows:
        media.delete(image_path)
        media.delete(audio_path)
    for video_path in video_paths:
        media.delete(video_path)


# ---------------- 剧集（挂在短剧下面） ----------------


@router.post(
    "/{drama_id}/episodes",
    response_model=EpisodeRead,
    status_code=status.HTTP_201_CREATED,
    summary="新增一集",
)
def create_episode(drama_id: int, payload: EpisodeCreate, db: DbSession, user: CurrentUser) -> Episode:
    drama = _get_owned_drama(db, drama_id, user)

    if payload.number is None:
        max_number = db.scalar(select(func.max(Episode.number)).where(Episode.drama_id == drama.id))
        number = (max_number or 0) + 1
    else:
        number = payload.number

    duplicated = db.scalar(
        select(Episode.id).where(Episode.drama_id == drama.id, Episode.number == number)
    )
    if duplicated is not None:
        raise ConflictError(f"第 {number} 集已存在")

    episode = Episode(
        drama_id=drama.id,
        number=number,
        title=payload.title,
        synopsis=payload.synopsis,
    )
    db.add(episode)
    db.commit()
    db.refresh(episode)
    return episode


@router.get("/{drama_id}/episodes", response_model=list[EpisodeRead], summary="某部短剧的剧集列表")
def list_episodes(drama_id: int, db: DbSession, user: CurrentUser) -> list[Episode]:
    _get_owned_drama(db, drama_id, user)
    stmt = select(Episode).where(Episode.drama_id == drama_id).order_by(Episode.number)
    return list(db.scalars(stmt))


@router.post(
    "/{drama_id}/synopsis/generate",
    response_model=SynopsisRead,
    summary="AI 起草某一集的大纲（只返回建议，不写库）",
)
async def generate_episode_synopsis(
    drama_id: int,
    payload: SynopsisGenerateRequest,
    db: DbSession,
    user: CurrentUser,
    provider: LlmProvider,
    quota: QuotaServiceDep,
) -> SynopsisRead:
    """把"本集标题 + 前几集内容"交给大模型，让它起草这一集的大纲。

    两个刻意的设计：
    1. **只返回建议，不写库** —— 大模型输出是概率性的，直接覆盖用户写好的内容不可接受；
    2. 它是 LLM 调用，所以**和生成分镜共用同一套配额与流水**（kind=synopsis），
       不然就出现了一个可以无限刷钱的口子。
    """
    drama = _get_owned_drama(db, drama_id, user)

    # 如果是给"已存在的某一集"起草大纲，就把这条流水挂到那一集上，便于对账
    log_episode_id: int | None = None
    if payload.after_number is not None:
        target = db.scalar(
            select(Episode).where(
                Episode.drama_id == drama.id,
                Episode.number == payload.after_number,
            )
        )
        if target is not None:
            log_episode_id = target.id

    # 注意 kind 必须和下面 record 时一致：
    # 检查用哪个额度、流水记成哪个类型，两者对不上额度就形同虚设
    # （这个 bug 正是被 test_ai_synopsis_consumes_quota 抓出来的）
    quota.ensure_allowed(db, user.id, kind="synopsis")
    log = quota.record(
        db,
        user_id=user.id,
        episode_id=log_episode_id,
        provider=provider.name,
        kind="synopsis",
    )

    try:
        synopsis, used_numbers = await generate_synopsis(
            db,
            drama=drama,
            title=payload.title,
            after_number=payload.after_number,
            provider=provider,
        )
    except Exception:
        QuotaService.finish(db, log, "failed")
        raise

    QuotaService.finish(db, log, "success")
    return SynopsisRead(synopsis=synopsis, used_context_episodes=used_numbers)
