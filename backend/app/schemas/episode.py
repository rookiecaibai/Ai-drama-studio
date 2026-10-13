"""剧集 Schema。"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.shot import ShotRead


class EpisodeCreate(BaseModel):
    title: str = Field(default="", max_length=120)
    synopsis: str = Field(default="")
    # 不填则自动取"当前最大集数 + 1"
    number: int | None = Field(default=None, ge=1, le=999)


class EpisodeUpdate(BaseModel):
    """修改剧集：只改传进来的字段（用户自己写大纲、改标题都走这个接口）。"""

    title: str | None = Field(default=None, max_length=120)
    synopsis: str | None = Field(default=None, max_length=2000)


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


class SynopsisGenerateRequest(BaseModel):
    """请求 AI 生成某一集的大纲。

    这个接口设计成"生成但不落库"：AI 只给建议，用户看过、改过、确认后再保存。
    为什么要这样：大模型输出是概率性的，直接覆盖用户写好的内容是不可接受的行为。
    """

    title: str = Field(default="", max_length=120, description="本集标题，可留空")
    # 生成"第 N 集"的大纲时，用编号小于 N 的剧集作为前情上下文。
    # 留空表示"接在现有所有剧集之后"（用于新建一集）。
    after_number: int | None = Field(default=None, ge=1, le=999)


class SynopsisRead(BaseModel):
    synopsis: str
    used_context_episodes: list[int] = []  # 这次生成参考了哪几集，让用户知道 AI 看了什么


class ImageGenerateRequest(BaseModel):
    """为某一集的镜头批量生成画面。"""

    only_missing: bool = Field(
        default=True,
        description="True=只生成还没有画面的镜头（省钱，默认）；False=全部重新生成",
    )


class AudioGenerateRequest(BaseModel):
    """为某一集的台词批量生成配音。"""

    only_missing: bool = Field(
        default=True,
        description="True=只生成还没有配音的镜头（默认）；False=全部重新生成",
    )
