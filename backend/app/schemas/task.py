"""生成任务 Schema。"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class GenerationTaskRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    episode_id: int
    status: str  # pending / running / succeeded / failed
    provider: str
    shot_count: int
    error: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
