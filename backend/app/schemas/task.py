"""生成任务 Schema。"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class GenerationTaskRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    # 任务有两种粒度：集级（分镜/画面/动态片段/配音/成片）有 episode_id；
    # 剧级（角色三视图）只有 drama_id、episode_id 为空。所以两个字段都必须可空，
    # 否则剧级任务的响应会直接 500（ResponseValidationError，我们踩过一次）。
    episode_id: int | None = None
    drama_id: int | None = None
    # shots=分镜 images=画面 clips=动态片段 audio=配音 video=成片 turnaround=角色三视图
    kind: str
    status: str  # pending / running / succeeded / failed
    provider: str
    shot_count: int
    total: int  # 本次任务要处理多少项
    done: int  # 已完成多少项（生成图片时可显示 3/6）
    error: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
