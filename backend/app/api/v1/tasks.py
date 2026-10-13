"""生成任务的查询接口。"""

from fastapi import APIRouter, Query
from sqlalchemy import select

from app.api.deps import CurrentUser, DbSession
from app.core.errors import NotFoundError
from app.db.models import GenerationTask, User
from app.schemas.task import GenerationTaskRead

router = APIRouter(prefix="/tasks", tags=["tasks"])


@router.get("/{task_id}", response_model=GenerationTaskRead, summary="查询任务状态（轮询这个接口）")
def get_task(task_id: int, db: DbSession, user: CurrentUser) -> GenerationTask:
    task = db.get(GenerationTask, task_id)
    if task is None or task.user_id != user.id:
        raise NotFoundError(f"任务 {task_id} 不存在")
    return task


@router.get("", response_model=list[GenerationTaskRead], summary="我的生成任务列表")
def list_tasks(
    db: DbSession,
    user: CurrentUser,
    episode_id: int | None = Query(default=None, description="按剧集筛选"),
) -> list[GenerationTask]:
    stmt = select(GenerationTask).where(GenerationTask.user_id == user.id)
    if episode_id is not None:
        stmt = stmt.where(GenerationTask.episode_id == episode_id)
    return list(db.scalars(stmt.order_by(GenerationTask.id.desc()).limit(50)))
