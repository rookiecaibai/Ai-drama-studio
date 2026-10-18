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
    # ── 角色卡 ──
    age: int | None = None
    appearance: str = ""
    occupation: str = ""
    background: str = ""
    # 锁定的外貌提示词：画镜头时每个出场角色都会带上它，这是"角色一致性"的关键
    look_prompt: str = ""
    # 三视图：有值表示已生成，通过 GET /api/v1/characters/{id}/turnaround 读取
    turnaround_path: str | None = None
    turnaround_error: str | None = None


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
    # ── 角色卡 ──
    age: int | None = Field(default=None, ge=0, le=200, description="具体年龄")
    appearance: str | None = Field(default=None, description="外貌：发型/脸型/体型/常穿的衣服")
    occupation: str | None = Field(default=None, max_length=60, description="职业")
    background: str | None = Field(default=None, description="背景故事")
    look_prompt: str | None = Field(
        default=None,
        description="锁定的外貌提示词。留空则由 appearance/age/occupation 自动生成",
    )


class VoiceOption(BaseModel):
    """可选音色。pitch_hz 是实测基频，帮助判断男女声（我听不到音频，只能测出来）。"""

    voice: str
    label: str
    pitch_hz: float | None = None


class TurnaroundGenerateRequest(BaseModel):
    """为一部剧的所有角色生成三视图（设定图）。

    默认只补缺：用户可能已经手动调好了某些角色的三视图，
    重新跑一遍会把满意的那张覆盖掉，所以默认跳过已有的。
    """

    only_missing: bool = Field(
        default=True,
        description="True=只给还没有三视图的角色生成（默认）；False=全部重新生成",
    )
