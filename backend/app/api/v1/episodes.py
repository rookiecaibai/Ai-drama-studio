"""剧集与 AI 生成任务相关接口（需登录，且只能操作自己的剧集）。"""

from fastapi import APIRouter, BackgroundTasks, status
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.api.deps import CurrentUser, DbSession, LlmProvider, QuotaServiceDep, SessionFactoryDep
from app.core.errors import NotFoundError
from app.db.models import Episode, GenerationTask, User
from app.schemas.episode import EpisodeDetail, GenerateRequest
from app.schemas.task import GenerationTaskRead
from app.services.task_service import run_generation_task

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
    response_model=GenerationTaskRead,
    status_code=status.HTTP_202_ACCEPTED,
    summary="创建 AI 生成任务（异步执行，用 GET /tasks/{id} 轮询进度）",
)
def create_generation_task(
    episode_id: int,
    payload: GenerateRequest,
    background_tasks: BackgroundTasks,
    db: DbSession,
    user: CurrentUser,
    provider: LlmProvider,
    quota: QuotaServiceDep,
    session_factory: SessionFactoryDep,
) -> GenerationTask:
    """设计变化：这个接口从"同步等结果"改成了"异步登记任务"。

    202 Accepted 的语义就是：我收下了，但还没做完，稍后来查。
    调用大模型要几十秒，HTTP 连接挂着干等又占资源又容易超时，
    所以立即返回任务 id，实际生成在后台执行，前端轮询任务状态。
    """
    episode = _get_owned_episode(db, episode_id, user)

    # 配额在"登记任务"时就检查和占用（失败也消耗，见 quota.py 的注释）
    quota.ensure_allowed(db, user.id)
    log = quota.record(db, user_id=user.id, episode_id=episode.id, provider=provider.name)

    task = GenerationTask(
        user_id=user.id,
        episode_id=episode.id,
        provider=provider.name,
        shot_count=payload.shot_count,
        extra_hint=payload.extra_hint,
    )
    db.add(task)
    db.commit()
    db.refresh(task)

    # 把真正耗时的生成丢到后台。注意传的是"会话工厂"而不是请求的会话：
    # 请求结束后请求会话会被关闭，后台任务必须开自己的会话。
    background_tasks.add_task(
        run_generation_task,
        task_id=task.id,
        log_id=log.id,
        provider=provider,
        session_factory=session_factory,
    )
    return task
