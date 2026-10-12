"""短剧 Schema。

为什么 Create / Update / Read 要分开写：
- Create 校验"必填什么"，Update 全是可选（局部更新），Read 带 id 和时间戳。
- 如果共用一个类，就会出现"创建时能传 id""返回时暴露内部字段"这类问题。
- 这类分层是 FastAPI 项目里区分专业和业余最明显的地方。
"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.episode import EpisodeRead


class DramaBase(BaseModel):
    title: str = Field(min_length=1, max_length=120, description="剧名")
    genre: str = Field(default="都市", max_length=40, description="题材")
    logline: str | None = Field(default=None, max_length=500, description="一句话梗概")
    style: str = Field(default="", max_length=200, description="视觉风格")


class DramaCreate(DramaBase):
    pass


class DramaUpdate(BaseModel):
    """局部更新：所有字段可选，只改传进来的字段。"""

    title: str | None = Field(default=None, min_length=1, max_length=120)
    genre: str | None = Field(default=None, max_length=40)
    logline: str | None = Field(default=None, max_length=500)
    style: str | None = Field(default=None, max_length=200)
    status: str | None = Field(default=None, max_length=20)


class DramaRead(DramaBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    status: str
    created_at: datetime
    updated_at: datetime


class DramaDetail(DramaRead):
    episodes: list[EpisodeRead] = []
