"""短剧与剧集相关接口（都需要登录，且只能操作自己的资源）。"""

from fastapi import APIRouter, BackgroundTasks, Query, status
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from app.api.deps import (
    CurrentUser,
    DbSession,
    ImageProviderDep,
    LlmProvider,
    QuotaServiceDep,
    SessionFactoryDep,
)
from app.core.config import get_settings
from app.core.errors import AppError, ConflictError, NotFoundError
from app.db.models import Character, DialogueLine, Drama, Episode, GenerationTask, Shot, User
from app.schemas.character import CharacterCreate, CharacterRead, TurnaroundGenerateRequest
from app.schemas.drama import DramaCreate, DramaDetail, DramaRead, DramaUpdate
from app.schemas.episode import (
    EpisodeCreate,
    EpisodeRead,
    SynopsisGenerateRequest,
    SynopsisRead,
)
from app.schemas.task import GenerationTaskRead
from app.services import media
from app.services import model_config
from app.services.cast_service import analyze_cast
from app.services.character_look import derive_age_group
from app.services.quota import QuotaService
from app.services.synopsis_service import generate_synopsis
from app.services.task_service import run_turnaround_task
from app.services.voice_profile import normalize_age_group, normalize_gender, profile_for

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
    # 逐句配音的文件也要清（一个镜头可能存了好几段单句音频）
    line_audio_paths = list(
        db.scalars(
            select(DialogueLine.audio_path)
            .join(Shot, DialogueLine.shot_id == Shot.id)
            .join(Episode, Shot.episode_id == Episode.id)
            .where(Episode.drama_id == drama.id, DialogueLine.audio_path.is_not(None))
        )
    )
    # 动态片段（图生视频的产物）同理 —— 视频文件大，更不能不删
    clip_paths = list(
        db.scalars(
            select(Shot.clip_path)
            .join(Episode, Shot.episode_id == Episode.id)
            .where(Episode.drama_id == drama.id, Shot.clip_path.is_not(None))
        )
    )

    db.delete(drama)  # cascade 会连带删掉剧集和镜头
    db.commit()

    for image_path, audio_path in media_rows:
        media.delete(image_path)
        media.delete(audio_path)
    for video_path in video_paths:
        media.delete(video_path)
    for line_audio_path in line_audio_paths:
        media.delete(line_audio_path)
    for clip_path in clip_paths:
        media.delete(clip_path)


@router.get("/{drama_id}/characters", response_model=list[CharacterRead], summary="这部剧的角色表")
def list_characters(drama_id: int, db: DbSession, user: CurrentUser) -> list[Character]:
    drama = _get_owned_drama(db, drama_id, user)
    return list(
        db.scalars(select(Character).where(Character.drama_id == drama.id).order_by(Character.id))
    )


@router.post(
    "/{drama_id}/characters",
    response_model=CharacterRead,
    status_code=status.HTTP_201_CREATED,
    summary="手动新增一个角色（后续集数出新角色时用）",
)
def create_character(
    drama_id: int, payload: CharacterCreate, db: DbSession, user: CurrentUser
) -> Character:
    """手动加一个角色。

    创建时会**顺手按性别/年龄推一套配音参数**（音色、音高、语速），
    所以新角色加完立刻就能配音、也能生成三视图，不用再跑一遍 AI 分析。

    同名会在数据库层被唯一约束拦住，但那样报的是 500；这里先查一次给出 409 和清楚的话 ——
    重名会破坏台词里的说话人匹配，属于用户该被明确提醒的错误。
    """
    drama = _get_owned_drama(db, drama_id, user)
    name = payload.name.strip()
    if not name:
        raise AppError("角色名不能为空", code="bad_character_name", status_code=400)

    duplicated = db.scalar(
        select(Character.id).where(Character.drama_id == drama.id, Character.name == name)
    )
    if duplicated is not None:
        raise ConflictError(f"这部剧已经有叫「{name}」的角色了")

    gender = normalize_gender(payload.gender or "unknown")
    # 年龄段优先用明确的，其次按年龄推导，最后兜底 young
    age_group = normalize_age_group(
        payload.age_group or derive_age_group(payload.age) or "young"
    )
    profile = profile_for(gender, age_group)

    character = Character(
        drama_id=drama.id,
        name=name[:60],
        gender=gender,
        age_group=age_group,
        age=payload.age,
        voice=profile.voice,
        pitch_shift=profile.pitch_shift,
        speed=profile.speed,
        personality=(payload.personality or "")[:500],
        appearance=(payload.appearance or "")[:500],
        occupation=(payload.occupation or "")[:60],
        background=(payload.background or "")[:2000],
    )
    db.add(character)
    db.commit()
    db.refresh(character)
    return character


@router.post(
    "/{drama_id}/characters/turnarounds",
    response_model=GenerationTaskRead,
    status_code=status.HTTP_202_ACCEPTED,
    summary="为这部剧的角色生成三视图（设定图，异步）",
)
def create_turnaround_task(
    drama_id: int,
    payload: TurnaroundGenerateRequest,
    background_tasks: BackgroundTasks,
    db: DbSession,
    user: CurrentUser,
    provider: ImageProviderDep,
    quota: QuotaServiceDep,
    session_factory: SessionFactoryDep,
) -> GenerationTask:
    """给角色画"三视图"：正面、侧面、背面并排的设定图。

    **它是画面的上游**：用户先把角色定下来（看得见长什么样），再去画每个镜头，
    这样"角色不对"的问题在花掉整集画面预算之前就暴露了。

    它是**剧级**任务（不属于某一集），所以任务行挂 `drama_id` 而不是 `episode_id`。
    """
    drama = _get_owned_drama(db, drama_id, user)

    # 用户的短剧里可能填了自己的文生图模型与 Key，那就用它（见 services/model_config.py）。
    # 没填则 fallback 到依赖注入进来的全局 provider —— 这也让测试的依赖覆盖继续生效。
    #
    # **三视图用横向画布**：它是"三个全身视角从左到右并排"，
    # 用镜头画面的竖屏尺寸会把三个人压成三条细条。
    provider = model_config.build_image(
        db, drama, get_settings(), fallback=provider, size_override=get_settings().image_sheet_size
    )

    quota.ensure_allowed(db, user.id, kind="turnaround")
    log = quota.record(
        db,
        user_id=user.id,
        # 剧级操作，不属于某一集 —— 这和"用户还没建剧就先起草大纲"是同一类情况，
        # 所以 episode_id 允许为空（流水表早就为此留了口子）。
        episode_id=None,
        provider=provider.name,
        kind="turnaround",
    )

    task = GenerationTask(
        user_id=user.id,
        drama_id=drama.id,
        episode_id=None,  # 剧级任务，不属于任何一集
        kind="turnaround",
        provider=provider.name,
        shot_count=0,
    )
    db.add(task)
    db.commit()
    db.refresh(task)

    background_tasks.add_task(
        run_turnaround_task,
        task_id=task.id,
        log_id=log.id,
        provider=provider,
        session_factory=session_factory,
        only_missing=payload.only_missing,
    )
    return task


@router.post(
    "/{drama_id}/cast/analyze",
    response_model=list[CharacterRead],
    summary="AI 分析角色表（配音的选角依据）",
)
async def analyze_drama_cast(
    drama_id: int,
    db: DbSession,
    user: CurrentUser,
    provider: LlmProvider,
    quota: QuotaServiceDep,
) -> list[Character]:
    """读剧本，判断每个角色的性别、年龄段、说话风格，并据此分配音色。

    为什么单独做一个接口（而不是藏在"生成配音"里）：
    这是需要**人工过目**的一步 —— AI 会猜错性别，也会把龙套判成主角。
    单独跑一次、让用户在界面上改完，再去配音，音色才不会错得离谱。
    （当然，直接点"生成配音"时如果还没有角色表，任务里也会自动先分析一次。）
    """
    drama = _get_owned_drama(db, drama_id, user)

    # 短剧级模型配置：用户填了自己的文本模型/Key 就用他的
    provider = model_config.build_llm(db, drama, get_settings(), fallback=provider)

    quota.ensure_allowed(db, user.id, kind="cast")
    log = quota.record(db, user_id=user.id, episode_id=None, provider=provider.name, kind="cast")
    try:
        characters = await analyze_cast(db, drama=drama, provider=provider)
    except Exception:
        quota.finish(db, log, "failed")
        raise
    quota.finish(db, log, "success")
    return characters


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

    # 短剧级模型配置：用户填了自己的文本模型/Key 就用他的
    provider = model_config.build_llm(db, drama, get_settings(), fallback=provider)

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
