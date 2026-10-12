"""镜头 Schema。"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ShotBase(BaseModel):
    shot_type: str = Field(default="中景", max_length=30, description="景别，如 特写/中景/远景")
    camera: str = Field(default="", max_length=60, description="运镜，如 推镜/跟拍/固定")
    description: str = Field(default="", description="画面描述")
    dialogue: str = Field(default="", description="台词或旁白")
    duration_sec: float = Field(default=3.0, gt=0, le=60, description="预计时长（秒）")
    image_prompt: str = Field(default="", description="给文生图模型的提示词")


class ShotRead(ShotBase):
    # from_attributes=True 让 Pydantic 能直接从 ORM 对象读属性，
    # 否则要手写一堆 {"a": obj.a, ...} 的转换代码
    model_config = ConfigDict(from_attributes=True)

    id: int
    number: int
    created_at: datetime
