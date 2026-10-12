"""短剧与剧集相关接口。"""

from fastapi import APIRouter, Query, status
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from app.api.deps import DbSession
from app.core.errors import ConflictError, NotFoundError
from app.db.models import Drama, Episode
from app.schemas.drama import DramaCreate, DramaDetail, DramaRead, DramaUpdate
from app.schemas.episode import EpisodeCreate, EpisodeRead

router = APIRouter(prefix="/dramas", tags=["dramas"])


def _get_drama_or_404(db: DbSession, drama_id: int) -> Drama:
    """把"取不到就 404"这种重复逻辑收成一个函数。"""
    drama = db.get(Drama, drama_id)
    if drama is None:
        raise NotFoundError(f"短剧 {drama_id} 不存在")
    return drama


@router.post("", response_model=DramaRead, status_code=status.HTTP_201_CREATED, summary="创建短剧")
def create_drama(payload: DramaCreate, db: DbSession) -> Drama:
    drama = Drama(**payload.model_dump())
    db.add(drama)
    db.commit()
    db.refresh(drama)  # 拿到数据库生成的 id / 时间戳
    return drama


@router.get("", response_model=list[DramaRead], summary="短剧列表")
def list_dramas(
    db: DbSession,
    offset: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
) -> list[Drama]:
    # 分页是硬要求：不加 limit 的列表接口，数据一多就是线上事故（内存打爆 + 慢查询）
    stmt = select(Drama).order_by(Drama.id.desc()).offset(offset).limit(limit)
    return list(db.scalars(stmt))


@router.get("/{drama_id}", response_model=DramaDetail, summary="短剧详情（含剧集列表）")
def get_drama(drama_id: int, db: DbSession) -> Drama:
    # selectinload：用一条额外的 IN 查询把剧集一起取回来，避免"N+1 查询"
    stmt = select(Drama).options(selectinload(Drama.episodes)).where(Drama.id == drama_id)
    drama = db.scalars(stmt).first()
    if drama is None:
        raise NotFoundError(f"短剧 {drama_id} 不存在")
    return drama


@router.patch("/{drama_id}", response_model=DramaRead, summary="局部更新短剧")
def update_drama(drama_id: int, payload: DramaUpdate, db: DbSession) -> Drama:
    drama = _get_drama_or_404(db, drama_id)
    # exclude_unset：只更新请求里真正出现过的字段（PATCH 语义）
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(drama, field, value)
    db.commit()
    db.refresh(drama)
    return drama


@router.delete("/{drama_id}", status_code=status.HTTP_204_NO_CONTENT, summary="删除短剧")
def delete_drama(drama_id: int, db: DbSession) -> None:
    drama = _get_drama_or_404(db, drama_id)
    db.delete(drama)  # cascade 会连带删掉剧集和镜头
    db.commit()


# ---------------- 剧集（挂在短剧下面） ----------------


@router.post(
    "/{drama_id}/episodes",
    response_model=EpisodeRead,
    status_code=status.HTTP_201_CREATED,
    summary="新增一集",
)
def create_episode(drama_id: int, payload: EpisodeCreate, db: DbSession) -> Episode:
    drama = _get_drama_or_404(db, drama_id)

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
def list_episodes(drama_id: int, db: DbSession) -> list[Episode]:
    _get_drama_or_404(db, drama_id)
    stmt = select(Episode).where(Episode.drama_id == drama_id).order_by(Episode.number)
    return list(db.scalars(stmt))
