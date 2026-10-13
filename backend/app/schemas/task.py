"""生成任务 Schema。"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class GenerationTaskRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    episode_id: int
    kind: str  # shots=生成分镜，images=生成画面
    status: str  # pending / running / succeeded / failed
    provider: str
    shot_count: int
    total: int  # 本次任务要处理多少项
    done: int  # 已完成多少项（生成图片时可显示 3/6）
    error: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
