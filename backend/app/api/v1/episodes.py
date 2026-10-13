"""剧集与 AI 生成相关接口（需登录，且只能操作自己的剧集）。"""

from fastapi import APIRouter
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.api.deps import CurrentUser, DbSession, LlmProvider, QuotaServiceDep
from app.core.errors import NotFoundError
from app.db.models import Episode, User
from app.schemas.episode import EpisodeDetail, GenerateRequest
from app.services.quota import QuotaService
from app.services.script_service import generate_episode_script

router = APIRouter(prefix="/episodes", tags=["episodes"])


def _get_owned_episode(db: DbSession, episode_id: int, user: User) -> Episode:
    episode = db.get(Episode, episode_id)
    # 与短剧同理：不属于你就当不存在（404），避免泄露资源是否存在
    if episode is None or episode.drama.owner_id != user.id:
        raise NotFoundError(f"剧集 {episode_id} 不存在")
    return episode


@router.get("/{episode_id}", response_model=EpisodeDetail, summary="剧集详情（含分镜）")
def get_episode(episode_id: int, db: DbSession, user: CurrentUser) -> Episode:
    _get_owned_episode(db, episode_id, user)
    stmt = select(Episode).options(selectinload(Episode.shots)).where(Episode.id == episode_id)
    return db.scalars(stmt).first()


@router.post(
    "/{episode_id}/generate",
    response_model=EpisodeDetail,
    summary="AI 生成本集剧本与分镜（可重复调用，幂等覆盖）",
)
async def generate_script(
    episode_id: int,
    payload: GenerateRequest,
    db: DbSession,
    user: CurrentUser,
    provider: LlmProvider,
    quota: QuotaServiceDep,
) -> Episode:
    # 注意这里是 async：因为要 await 大模型调用。
    # 如果写成同步函数，FastAPI 会把它丢到线程池跑，虽然也能用，
    # 但等待网络 IO 时白白占一个线程；异步函数才能真正做到"等待期间不占资源"。
    episode = _get_owned_episode(db, episode_id, user)

    # 配额：先检查、再占额度（调用前记 pending 流水，失败也消耗额度 ——
    # 失败请求同样要给模型厂商付费，不消耗的话等于鼓励重试刷接口）
    quota.ensure_allowed(db, user.id)
    log = quota.record(db, user_id=user.id, episode_id=episode.id, provider=provider.name)

    try:
        result = await generate_episode_script(
            db,
            episode=episode,
            drama=episode.drama,
            provider=provider,
            shot_count=payload.shot_count,
            extra_hint=payload.extra_hint,
        )
    except Exception:
        QuotaService.finish(db, log, "failed")
        raise
    else:
        QuotaService.finish(db, log, "success")
        return result
