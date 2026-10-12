"""剧集与 AI 生成相关接口。"""

from fastapi import APIRouter
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.api.deps import DbSession, LlmProvider
from app.core.errors import NotFoundError
from app.db.models import Episode
from app.schemas.episode import EpisodeDetail, GenerateRequest
from app.services.script_service import generate_episode_script

router = APIRouter(prefix="/episodes", tags=["episodes"])


def _get_episode_or_404(db: DbSession, episode_id: int) -> Episode:
    episode = db.get(Episode, episode_id)
    if episode is None:
        raise NotFoundError(f"剧集 {episode_id} 不存在")
    return episode


@router.get("/{episode_id}", response_model=EpisodeDetail, summary="剧集详情（含分镜）")
def get_episode(episode_id: int, db: DbSession) -> Episode:
    stmt = select(Episode).options(selectinload(Episode.shots)).where(Episode.id == episode_id)
    episode = db.scalars(stmt).first()
    if episode is None:
        raise NotFoundError(f"剧集 {episode_id} 不存在")
    return episode


@router.post(
    "/{episode_id}/generate",
    response_model=EpisodeDetail,
    summary="AI 生成本集剧本与分镜（可重复调用，幂等覆盖）",
)
async def generate_script(
    episode_id: int,
    payload: GenerateRequest,
    db: DbSession,
    provider: LlmProvider,
) -> Episode:
    # 注意这里是 async：因为要 await 大模型调用。
    # 如果写成同步函数，FastAPI 会把它丢到线程池跑，虽然也能用，
    # 但等待网络 IO 时白白占一个线程；异步函数才能真正做到"等待期间不占资源"。
    episode = _get_episode_or_404(db, episode_id)
    return await generate_episode_script(
        db,
        episode=episode,
        drama=episode.drama,
        provider=provider,
        shot_count=payload.shot_count,
        extra_hint=payload.extra_hint,
    )
