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
    # 画面生成结果：image_path 不为空表示已经有图了（前端据此决定显示图片还是待生成）
    # 注意这里给的是"有没有图"，不是可直接访问的 URL ——
    # 图片必须通过带鉴权的 GET /api/v1/shots/{id}/image 读取
    image_path: str | None = None
    image_error: str | None = None
    # 配音同理：走 GET /api/v1/shots/{id}/audio
    audio_path: str | None = None
    audio_error: str | None = None
