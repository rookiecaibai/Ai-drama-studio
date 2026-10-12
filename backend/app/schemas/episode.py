"""剧集 Schema。"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.shot import ShotRead


class EpisodeCreate(BaseModel):
    title: str = Field(default="", max_length=120)
    synopsis: str = Field(default="")
    # 不填则自动取"当前最大集数 + 1"
    number: int | None = Field(default=None, ge=1, le=999)


class EpisodeRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    drama_id: int
    number: int
    title: str
    synopsis: str
    status: str
    created_at: datetime
    updated_at: datetime


class EpisodeDetail(EpisodeRead):
    """详情比列表多带镜头列表，避免列表接口返回巨量数据。"""

    shots: list[ShotRead] = []


class GenerateRequest(BaseModel):
    shot_count: int = Field(default=6, ge=1, le=20, description="希望生成多少个镜头")
    extra_hint: str = Field(default="", max_length=500, description="额外要求，会拼进提示词")
