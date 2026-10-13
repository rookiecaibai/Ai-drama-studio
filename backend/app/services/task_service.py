"""异步生成任务的执行器。

架构说明（为什么这样设计）：
1. 接口层只做"登记"：建一条 pending 任务、占额度、把任务丢进后台队列，立即返回 202；
2. 实际生成在 BackgroundTasks 里跑，用的数据库会话是**自己新开的** ——
   请求结束后的会话会被关闭，绝不能把请求里的会话带进后台任务；
3. 任务状态机：pending -> running -> succeeded / failed，全程落库，
   前端轮询 /tasks/{id} 就能看到进度。

诚实的局限（面试会问"然后呢"）：
BackgroundTasks 只是"进程内"的后台任务 —— 服务重启，没跑完的任务就丢了；
多实例部署时任务只在其中一个实例上跑。生产级方案是把任务投到
消息队列（Celery / arq / RQ + Redis），由独立的 worker 进程消费。
我们这个实现是最小可用的版本，但状态机、轮询接口、幂等语义
和生产方案是完全一致的 —— 换队列时业务代码不用动。
"""

import logging
from datetime import datetime, timezone

from sqlalchemy import select, update
from sqlalchemy.orm import Session, sessionmaker

from app.db.models import Episode, GenerationLog, GenerationTask, Shot
from app.services.image_service import generate_images_for_episode
from app.services.images.base import ImageProvider
from app.services.llm.base import LLMProvider
from app.services.script_service import generate_episode_script
from app.services.tts.base import TtsProvider
from app.services.tts_service import generate_audio_for_episode
from app.services.video_service import compose_episode_video

logger = logging.getLogger(__name__)


def _now() -> datetime:
    # 与数据库端 server_default=now() 的 naive UTC 保持一致
    return datetime.now(timezone.utc).replace(tzinfo=None)


def mark_interrupted_tasks(session_factory: sessionmaker[Session] | None = None) -> int:
    """把上次运行遗留的 pending/running 任务标记为失败（服务启动时调用）。

    为什么必须有这一步：
    任务状态存在数据库里，但"正在执行"这件事只存在于**进程内存**中。
    服务一旦重启（崩溃、部署、按停止键），数据库里那些任务会永远是 running ——
    前端一直转圈，用户以为还在跑，其实永远不会完成了。
    这就是本文档开头那条"诚实局限"的具体修法：状态机在库里，执行在进程里，
    两者不一致时必须由启动流程来对账。

    返回被清理的任务数。
    """
    if session_factory is None:
        from app.db.session import get_session_factory

        session_factory = get_session_factory()

    with session_factory() as db:
        tasks = (
            db.execute(
                update(GenerationTask)
                .where(GenerationTask.status.in_(("pending", "running")))
                .values(
                    status="failed",
                    error="服务重启导致任务中断，请重新生成",
                    finished_at=_now(),
                )
            ).rowcount
            or 0
        )
        # 调用流水同理：还留在 pending 的，实际上都已经结束了（没成功就是失败）
        logs = (
            db.execute(
                update(GenerationLog).where(GenerationLog.status == "pending").values(status="failed")
            ).rowcount
            or 0
        )
        db.commit()

    if tasks or logs:
        logger.warning("启动清理：%s 个中断的任务、%s 条未完成的调用流水已标记为失败", tasks, logs)
    return tasks


def _finish_log(db: Session, log_id: int, status: str) -> None:
    log = db.get(GenerationLog, log_id)
    if log is not None:
        log.status = status
        db.commit()


async def run_generation_task(
    *,
    task_id: int,
    log_id: int,
    provider: LLMProvider,
    session_factory: sessionmaker[Session],
) -> None:
    """后台执行一次生成任务，并把状态/结果写回任务和流水。"""
    with session_factory() as db:
        task = db.get(GenerationTask, task_id)
        if task is None:
            logger.warning("任务 %s 不存在，跳过", task_id)
            return

        task.status = "running"
        task.started_at = _now()
        db.commit()
        logger.info("任务 %s 开始执行（provider=%s）", task_id, provider.name)

        try:
            episode = db.get(Episode, task.episode_id)
            await generate_episode_script(
                db,
                episode=episode,
                drama=episode.drama,
                provider=provider,
                shot_count=task.shot_count,
                extra_hint=task.extra_hint,
            )
        except Exception as exc:  # noqa: BLE001 —— 后台任务必须兜住一切异常并落库
            task.status = "failed"
            task.error = str(exc)[:500]
            task.finished_at = _now()
            db.commit()
            _finish_log(db, log_id, "failed")
            logger.exception("任务 %s 失败", task_id)
            return

        task.status = "succeeded"
        task.finished_at = _now()
        db.commit()
        _finish_log(db, log_id, "success")
        logger.info("任务 %s 完成", task_id)


async def run_image_task(
    *,
    task_id: int,
    log_id: int,
    provider: ImageProvider,
    session_factory: sessionmaker[Session],
    only_missing: bool = True,
) -> None:
    """后台为某一集的镜头批量生成画面。

    和生成分镜共用同一张任务表、同一套状态机、同一个轮询接口 ——
    前端不需要为"图片任务"写第二套逻辑。这正是把任务抽象成 kind 的价值。
    """
    with session_factory() as db:
        task = db.get(GenerationTask, task_id)
        if task is None:
            logger.warning("任务 %s 不存在，跳过", task_id)
            return

        task.status = "running"
        task.started_at = _now()
        db.commit()
        logger.info("任务 %s 开始生成画面（provider=%s，only_missing=%s）", task_id, provider.name, only_missing)

        def _on_total(total: int) -> None:
            task.total = total
            db.commit()

        def _on_progress() -> None:
            task.done += 1
            db.commit()

        try:
            episode = db.get(Episode, task.episode_id)
            planned, succeeded = await generate_images_for_episode(
                db,
                episode=episode,
                provider=provider,
                only_missing=only_missing,
                on_total=_on_total,
                on_progress=_on_progress,
            )
        except Exception as exc:  # noqa: BLE001
            task.status = "failed"
            task.error = str(exc)[:500]
            task.finished_at = _now()
            db.commit()
            _finish_log(db, log_id, "failed")
            logger.exception("任务 %s 失败", task_id)
            return

        # 全部失败和"部分成功"要区分：全挂了对用户来说就是失败
        if planned > 0 and succeeded == 0:
            task.status = "failed"
            task.error = f"{planned} 张画面全部生成失败，请查看各镜头的说明"
            _finish_log(db, log_id, "failed")
        else:
            task.status = "succeeded"
            _finish_log(db, log_id, "success")

        task.finished_at = _now()
        db.commit()
        logger.info("任务 %s 结束：计划 %s 张，成功 %s 张", task_id, planned, succeeded)


async def run_audio_task(
    *,
    task_id: int,
    log_id: int,
    provider: TtsProvider,
    session_factory: sessionmaker[Session],
    only_missing: bool = True,
) -> None:
    """后台为某一集的台词批量生成配音。

    和图片任务同构：同一张任务表、同一套状态机、同一个轮询接口，只是 kind=audio。
    """
    with session_factory() as db:
        task = db.get(GenerationTask, task_id)
        if task is None:
            logger.warning("任务 %s 不存在，跳过", task_id)
            return

        task.status = "running"
        task.started_at = _now()
        db.commit()
        logger.info("任务 %s 开始生成配音（provider=%s）", task_id, provider.name)

        def _on_total(total: int) -> None:
            task.total = total
            db.commit()

        def _on_progress() -> None:
            task.done += 1
            db.commit()

        try:
            episode = db.get(Episode, task.episode_id)
            planned, succeeded = await generate_audio_for_episode(
                db,
                episode=episode,
                provider=provider,
                only_missing=only_missing,
                on_total=_on_total,
                on_progress=_on_progress,
            )
        except Exception as exc:  # noqa: BLE001
            task.status = "failed"
            task.error = str(exc)[:500]
            task.finished_at = _now()
            db.commit()
            _finish_log(db, log_id, "failed")
            logger.exception("任务 %s 失败", task_id)
            return

        if planned > 0 and succeeded == 0:
            task.status = "failed"
            task.error = f"{planned} 段配音全部生成失败，请查看各镜头的说明"
            _finish_log(db, log_id, "failed")
        else:
            task.status = "succeeded"
            _finish_log(db, log_id, "success")

        task.finished_at = _now()
        db.commit()
        logger.info("任务 %s 结束：计划 %s 段，成功 %s 段", task_id, planned, succeeded)


def run_video_task(
    *,
    task_id: int,
    log_id: int,
    session_factory: sessionmaker[Session],
) -> None:
    """后台把一集的镜头合成成片。

    注意这是**同步函数**（不是 async）：
    ffmpeg 是外部进程 + 阻塞等待，写成 async 会卡住事件循环，把整个服务拖死。
    写成同步函数后，Starlette 会自动把它丢到线程池里跑 —— 这是正确的做法。
    （图片/配音任务之所以是 async，是因为它们在等网络 IO。）
    """
    with session_factory() as db:
        task = db.get(GenerationTask, task_id)
        if task is None:
            logger.warning("任务 %s 不存在，跳过", task_id)
            return

        task.status = "running"
        task.started_at = _now()
        db.commit()
        logger.info("任务 %s 开始合成视频", task_id)

        def _on_total(total: int) -> None:
            task.total = total
            db.commit()

        def _on_progress() -> None:
            task.done += 1
            db.commit()

        episode = db.get(Episode, task.episode_id)
        try:
            shots = list(
                db.scalars(select(Shot).where(Shot.episode_id == episode.id).order_by(Shot.number))
            )
            rel_path = compose_episode_video(
                episode=episode,
                shots=shots,
                on_total=_on_total,
                on_progress=_on_progress,
            )
        except Exception as exc:  # noqa: BLE001
            if episode is not None:
                episode.video_error = str(exc)[:500]
            task.status = "failed"
            task.error = str(exc)[:500]
            task.finished_at = _now()
            db.commit()
            _finish_log(db, log_id, "failed")
            logger.exception("任务 %s 失败", task_id)
            return

        episode.video_path = rel_path
        episode.video_error = None
        task.status = "succeeded"
        task.finished_at = _now()
        db.commit()
        _finish_log(db, log_id, "success")
        logger.info("任务 %s 合成完成: %s", task_id, rel_path)
