"""角色表 Schema。

注意：`CharacterUpdate` 里所有字段都可选 —— 前端一次只改一项（比如只改音色）时
不该要求把整个角色对象回传，那样容易把别的字段意外覆盖成默认值。
"""

from pydantic import BaseModel, ConfigDict, Field


class CharacterRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    drama_id: int
    name: str
    gender: str
    age_group: str
    voice: str
    pitch_shift: float
    speed: float
    personality: str
    notes: str


class CharacterUpdate(BaseModel):
    """手动修改角色。

    为什么需要它：AI 会猜错（名字看不出性别、年龄只能靠剧情推测），
    而且每个人对音色的偏好不同。所以角色表是"AI 打底 + 人工可改"的数据。
    """

    gender: str | None = Field(default=None, description="male / female / unknown")
    age_group: str | None = Field(default=None, description="child / teen / young / middle / elderly")
    voice: str | None = Field(default=None, description="音色 id，见 GET /characters/voices")
    speed: float | None = Field(default=None, ge=0.5, le=2.0, description="基础语速")
    pitch_shift: float | None = Field(
        default=None, ge=-0.25, le=0.25, description="音高偏移：0.06 = 升高 6%"
    )
    personality: str | None = None
    notes: str | None = None


class VoiceOption(BaseModel):
    """可选音色。pitch_hz 是实测基频，帮助判断男女声（我听不到音频，只能测出来）。"""

    voice: str
    label: str
    pitch_hz: float | None = None
