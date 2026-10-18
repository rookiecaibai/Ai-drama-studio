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


class DialogueLineRead(BaseModel):
    """镜头里的一句台词：谁说的、什么情绪、合成到哪一步。"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    index: int
    speaker: str
    character_id: int | None = None
    text: str  # 只含要朗读的内容（角色名已经被解析层摘掉了）
    cue: str
    emotion: str  # AI 判断的情绪，如 "急迫"
    intensity: float
    speed: float | None = None  # 最终语速；None = 还没做情绪分析（此时用角色基础语速）
    audio_path: str | None = None
    audio_error: str | None = None


class ShotUpdate(BaseModel):
    """用户手动修改一个镜头（在分镜表里直接编辑）。

    为什么需要它：AI 生成的分镜只是"初稿"。用户最常做的修改就是
    改画面描述、改台词、调时长 —— 不给这个能力，用户只能反复重生成整集。

    **关于台词的特殊处理**（在 API 层实现）：
    `dialogue` 是这个镜头里最"派生"的东西 —— 角色分配、情绪判断、语速、
    每句音频全是从它解析出来的。所以改了台词，派生出来的一切都要作废，
    否则会出现"台词改了，配音还是老台词"这种自相矛盾的状态。
    改画面描述/提示词则**不会**作废旧图（图还在，只是可能不匹配了，用户可自行重生成）。
    """

    shot_type: str | None = Field(default=None, max_length=30)
    camera: str | None = Field(default=None, max_length=60)
    description: str | None = None
    dialogue: str | None = None
    duration_sec: float | None = Field(default=None, gt=0, le=60)
    image_prompt: str | None = None
    audio_prompt: str | None = None


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
    # 动态片段（图生视频）：不为空表示这个镜头的画面**会动**，
    # 可通过 GET /api/v1/shots/{id}/clip 预览。
    # 为空时合成环节会用静态图 —— 所以前端可以据此提示"这个镜头还是静图"。
    clip_path: str | None = None
    clip_error: str | None = None
    # 逐句台词：前端据此显示"谁说的、什么情绪"，也是多角色配音的依据
    lines: list[DialogueLineRead] = []
