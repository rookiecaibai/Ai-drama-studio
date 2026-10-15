"""剧集与 AI 生成任务相关接口（需登录，且只能操作自己的剧集）。"""

from fastapi import APIRouter, BackgroundTasks, status
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.api.deps import (
    ClipProviderDep,
    CurrentUser,
    DbSession,
    ImageProviderDep,
    LlmProvider,
    QuotaServiceDep,
    SessionFactoryDep,
    TtsProviderDep,
)
from app.core.errors import AppError, NotFoundError
from app.db.models import Episode, GenerationTask, User
from app.schemas.episode import (
    AudioGenerateRequest,
    ClipGenerateRequest,
    EpisodeDetail,
    EpisodeUpdate,
    GenerateRequest,
    ImageGenerateRequest,
)
from app.schemas.task import GenerationTaskRead
from app.services import media
from app.services.task_service import (
    run_audio_task,
    run_clip_task,
    run_generation_task,
    run_image_task,
    run_video_task,
)

router = APIRouter(prefix="/episodes", tags=["episodes"])


def _get_owned_episode(db: DbSession, episode_id: int, user: User) -> Episode:
    episode = db.get(Episode, episode_id)
    # 与短剧同理：不属于你就当不存在（404），避免泄露资源是否存在
    if episode is None or episode.drama.owner_id != user.id:
        raise NotFoundError(f"剧集 {episode_id} 不存在")
    return episode


@router.patch("/{episode_id}", response_model=EpisodeDetail, summary="修改本集标题或大纲")
def update_episode(
    episode_id: int,
    payload: EpisodeUpdate,
    db: DbSession,
    user: CurrentUser,
) -> Episode:
    """用户自己写大纲、改标题都走这个接口。

    注意：这是**用户的内容**，AI 生成分镜时只读它、不会覆盖它
    （见 script_service 里"仅当为空才填"的逻辑）。
    """
    episode = _get_owned_episode(db, episode_id, user)
    data = payload.model_dump(exclude_unset=True)
    if data.get("title") is not None:
        episode.title = data["title"].strip()
    if data.get("synopsis") is not None:
        episode.synopsis = data["synopsis"].strip()
    db.commit()
    db.refresh(episode)
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


@router.post(
    "/{episode_id}/images",
    response_model=GenerationTaskRead,
    status_code=status.HTTP_202_ACCEPTED,
    summary="为这一集的镜头批量生成画面（异步，用 GET /tasks/{id} 轮询进度）",
)
def create_image_task(
    episode_id: int,
    payload: ImageGenerateRequest,
    background_tasks: BackgroundTasks,
    db: DbSession,
    user: CurrentUser,
    provider: ImageProviderDep,
    quota: QuotaServiceDep,
    session_factory: SessionFactoryDep,
) -> GenerationTask:
    """文生图：把每个镜头的 image_prompt 变成一张画面。

    和生成分镜是完全一样的套路：登记任务 -> 202 -> 后台跑 -> 前端轮询。
    区别只在 kind=images，以及配额走 DAILY_IMAGE_LIMIT（图片贵得多，限额单独设）。
    """
    episode = _get_owned_episode(db, episode_id, user)

    quota.ensure_allowed(db, user.id, kind="images")
    log = quota.record(
        db,
        user_id=user.id,
        episode_id=episode.id,
        provider=provider.name,
        kind="images",
    )

    task = GenerationTask(
        user_id=user.id,
        episode_id=episode.id,
        kind="images",
        provider=provider.name,
        shot_count=0,
    )
    db.add(task)
    db.commit()
    db.refresh(task)

    background_tasks.add_task(
        run_image_task,
        task_id=task.id,
        log_id=log.id,
        provider=provider,
        session_factory=session_factory,
        only_missing=payload.only_missing,
    )
    return task


@router.post(
    "/{episode_id}/audio",
    response_model=GenerationTaskRead,
    status_code=status.HTTP_202_ACCEPTED,
    summary="为这一集的台词批量生成配音（异步，用 GET /tasks/{id} 轮询进度）",
)
def create_audio_task(
    episode_id: int,
    payload: AudioGenerateRequest,
    background_tasks: BackgroundTasks,
    db: DbSession,
    user: CurrentUser,
    provider: TtsProviderDep,
    llm: LlmProvider,
    quota: QuotaServiceDep,
    session_factory: SessionFactoryDep,
) -> GenerationTask:
    """把每个镜头的台词变成语音。

    **逐句配音**：一个镜头里可能有好几个人说话（`陈默：…` / `林晚：…`），
    每一句会用各自角色的音色、语速、音高分别合成，再拼成这个镜头的音频。
    所以这个任务除了 TTS，还会用大模型做两件事：分析角色表、逐句判断情绪语气。
    只有**有台词**的镜头才算进 total：纯画面镜头没话可说。
    """
    episode = _get_owned_episode(db, episode_id, user)

    quota.ensure_allowed(db, user.id, kind="audio")
    log = quota.record(
        db,
        user_id=user.id,
        episode_id=episode.id,
        provider=provider.name,
        kind="audio",
    )

    task = GenerationTask(
        user_id=user.id,
        episode_id=episode.id,
        kind="audio",
        provider=provider.name,
        shot_count=0,
    )
    db.add(task)
    db.commit()
    db.refresh(task)

    background_tasks.add_task(
        run_audio_task,
        task_id=task.id,
        log_id=log.id,
        provider=provider,
        session_factory=session_factory,
        only_missing=payload.only_missing,
        llm=llm,
    )
    return task


@router.post(
    "/{episode_id}/clips",
    response_model=GenerationTaskRead,
    status_code=status.HTTP_202_ACCEPTED,
    summary="为每个镜头的画面生成动态片段（图生视频，异步）",
)
def create_clip_task(
    episode_id: int,
    payload: ClipGenerateRequest,
    background_tasks: BackgroundTasks,
    db: DbSession,
    user: CurrentUser,
    provider: ClipProviderDep,
    quota: QuotaServiceDep,
    session_factory: SessionFactoryDep,
) -> GenerationTask:
    """把每个镜头的**静图**变成一段真的会动的短片。

    不做这一步，成片就是"每张图定格 + 配音"的幻灯片 —— 画面完全静止。
    这一步是可选的：没有它，合成环节会自动退回用静态图，成片照样能出。

    注意这个任务**可能很慢**（真实视频模型几十秒到几分钟一条），
    所以返回的进度（done/total）要盯着看 —— 前端轮询 /tasks/{id} 就能看到。
    """
    episode = _get_owned_episode(db, episode_id, user)

    if provider is None:
        raise AppError(
            "当前没有启用图生视频（VIDEO_CLIP_PROVIDER=none）。"
            "可选：mock（本地运镜，免费）或 openai_compat（真实视频模型）",
            code="clip_provider_disabled",
            status_code=400,
        )

    quota.ensure_allowed(db, user.id, kind="clips")
    log = quota.record(
        db,
        user_id=user.id,
        episode_id=episode.id,
        provider=provider.name,
        kind="clips",
    )

    task = GenerationTask(
        user_id=user.id,
        episode_id=episode.id,
        kind="clips",
        provider=provider.name,
        shot_count=0,
    )
    db.add(task)
    db.commit()
    db.refresh(task)

    background_tasks.add_task(
        run_clip_task,
        task_id=task.id,
        log_id=log.id,
        provider=provider,
        session_factory=session_factory,
        only_missing=payload.only_missing,
    )
    return task


@router.post(
    "/{episode_id}/video",
    response_model=GenerationTaskRead,
    status_code=status.HTTP_202_ACCEPTED,
    summary="把这一集合成成竖屏 MP4（异步，用 GET /tasks/{id} 轮询进度）",
)
def create_video_task(
    episode_id: int,
    background_tasks: BackgroundTasks,
    db: DbSession,
    user: CurrentUser,
    quota: QuotaServiceDep,
    session_factory: SessionFactoryDep,
) -> GenerationTask:
    """本地 ffmpeg 合成，不需要任何外部服务。

    也走配额（kind=video）：这里限的不是钱，而是**服务器 CPU** ——
    视频编码是重活，不限制的话几个请求就能把机器压满，把别人的接口一起拖慢。
    """
    episode = _get_owned_episode(db, episode_id, user)

    quota.ensure_allowed(db, user.id, kind="video")
    log = quota.record(
        db,
        user_id=user.id,
        episode_id=episode.id,
        provider="ffmpeg",
        kind="video",
    )

    task = GenerationTask(
        user_id=user.id,
        episode_id=episode.id,
        kind="video",
        provider="ffmpeg",
        shot_count=0,
    )
    db.add(task)
    db.commit()
    db.refresh(task)

    background_tasks.add_task(
        run_video_task,
        task_id=task.id,
        log_id=log.id,
        session_factory=session_factory,
    )
    return task


@router.get("/{episode_id}/video", summary="播放/下载这一集的成片（需登录，只能看自己的）")
def get_episode_video(episode_id: int, db: DbSession, user: CurrentUser) -> FileResponse:
    """和图片/音频同理：成片也是私有内容，必须走鉴权接口。

    `Accept-Ranges: none` 是刻意加的：我们不实现 HTTP Range（分段下载），
    而浏览器看到 Content-Length 会以为支持续传。明确声明不支持，播放器行为更可预期。
    （编码时加了 -movflags +faststart，元数据在文件开头，所以不需要 Range 也能边下边播。）
    """
    episode = _get_owned_episode(db, episode_id, user)

    if not episode.video_path or not media.exists(episode.video_path):
        raise NotFoundError("这一集还没有合成视频")

    return FileResponse(
        media.absolute_path(episode.video_path),
        media_type="video/mp4",
        headers={"Cache-Control": "no-cache", "Accept-Ranges": "none"},
    )
