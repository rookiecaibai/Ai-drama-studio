"""数据模型：短剧(Drama) -> 剧集(Episode) -> 镜头(Shot)。

为什么是三层而不是一张大表：
- 短剧是一个"作品"，会有多集；每集有独立标题/梗概/状态；每集又切分成若干镜头。
- 拆表后，生成脚本只需要动 Shot 表，删除短剧靠外键级联清理，语义清晰。
- 这也是短剧行业真实的业务结构，面试时能讲清"为什么这样建模"比"我用了 ORM"更值钱。
"""

from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class TimestampMixin:
    """创建/更新时间。审计字段几乎是所有业务表的标配。"""

    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=False
    )


class User(TimestampMixin, Base):
    """用户账号。"""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(50), unique=True, index=True, nullable=False)
    email: Mapped[str | None] = mapped_column(String(120), default=None)
    # 只存 bcrypt 哈希，绝不存明文密码（见 core/security.py 的解释）
    password_hash: Mapped[str] = mapped_column(String(200), nullable=False)
    is_active: Mapped[bool] = mapped_column(default=True, nullable=False)

    dramas: Mapped[list["Drama"]] = relationship(
        back_populates="owner",
        cascade="all, delete-orphan",
        order_by="Drama.id",
    )


class Drama(TimestampMixin, Base):
    __tablename__ = "dramas"

    id: Mapped[int] = mapped_column(primary_key=True)
    # 归属：每个用户只能看到和操作自己的短剧
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(120), nullable=False)
    genre: Mapped[str] = mapped_column(String(40), default="都市", nullable=False)
    logline: Mapped[str | None] = mapped_column(String(500))  # 一句话梗概，可为空
    style: Mapped[str] = mapped_column(String(200), default="", nullable=False)  # 视觉/叙事风格
    status: Mapped[str] = mapped_column(String(20), default="draft", nullable=False)  # draft/ready

    # cascade：删短剧时连带删剧集。
    # 这里刻意用默认的懒加载（lazy="select"），需要关联数据的地方用 selectinload 显式声明。
    # 为什么不用 lazy="selectin" 一劳永逸？因为列表接口根本不需要 episodes，
    # 设成 selectin 会让每次翻页都多查一次表 —— 隐式行为比显式加载更容易埋性能坑。
    owner: Mapped[User] = relationship(back_populates="dramas")
    episodes: Mapped[list["Episode"]] = relationship(
        back_populates="drama",
        cascade="all, delete-orphan",
        order_by="Episode.number",
    )
    # 角色属于"剧"而不是"集"：同一个人物会贯穿全剧，音色配置不该每集重来一遍
    characters: Mapped[list["Character"]] = relationship(
        back_populates="drama",
        cascade="all, delete-orphan",
        order_by="Character.id",
    )


class Character(TimestampMixin, Base):
    """剧中的角色 —— 配音时的"选角表"。

    为什么需要它（而不是每次配音都猜）：
    一个镜头里可能有好几个人说话（`陈默：…` / `林晚：…`）。
    要让每个人有自己的声音，就必须先有一份"谁是谁"的档案：
    性别、年龄段、性格，然后据此分配音色、音高、语速。

    这份档案由 AI 先分析一遍（读剧名/梗概/大纲/台词），但**人可以改** ——
    AI 会猜错性别，人也会想换音色，所以它是可编辑的数据，不是写死的常量。
    """

    __tablename__ = "characters"
    __table_args__ = (UniqueConstraint("drama_id", "name", name="uq_character_drama_name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    drama_id: Mapped[int] = mapped_column(ForeignKey("dramas.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(60), nullable=False)  # 要和台词里的说话人写法对得上
    gender: Mapped[str] = mapped_column(String(10), default="unknown", nullable=False)  # male/female/unknown
    age_group: Mapped[str] = mapped_column(
        String(10), default="young", nullable=False
    )  # child/teen/young/middle/elderly
    voice: Mapped[str] = mapped_column(String(40), default="", nullable=False)  # 音色 id
    # 音高偏移（0.06 = 升高 6%）。只有一个男声可用时，靠它派生出"年轻/中年/年老"的差别
    pitch_shift: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    speed: Mapped[float] = mapped_column(Float, default=1.0, nullable=False)  # 基础语速
    personality: Mapped[str] = mapped_column(Text, default="", nullable=False)  # 性格/说话风格（喂给情感分析）
    notes: Mapped[str] = mapped_column(Text, default="", nullable=False)

    # ── 角色卡：给"画角色"和"画镜头"用的信息 ──
    #
    # 为什么要单独存一份"外貌"，而不是让模型每次自己发挥：
    # 同一个角色要在很多镜头里出现，长相必须稳定。但**文生图模型没有记忆** ——
    # 每次调用都是全新的生成。所以唯一的办法是把外貌固定成一段文字，
    # 画每个镜头时都把它带上（见 look_prompt）。这是当前厂商不支持参考图时
    # 唯一能做到的"角色一致性"手段，也是最朴素有效的那种。
    age: Mapped[int | None] = mapped_column(default=None)  # 具体年龄（如 28）；age_group 仍用于选音色
    appearance: Mapped[str] = mapped_column(
        Text, default="", nullable=False
    )  # 外貌：发型/脸型/体型/常穿的衣服
    occupation: Mapped[str] = mapped_column(String(60), default="", nullable=False)  # 职业
    background: Mapped[str] = mapped_column(Text, default="", nullable=False)  # 背景故事
    # 锁定的外貌提示词：**所有镜头画这个角色时都会带上它**。
    # 为空时由 appearance / age / occupation 自动拼出来；用户也可以手改（改了就以后者为锚）。
    look_prompt: Mapped[str] = mapped_column(Text, default="", nullable=False)
    # 角色三视图（正面/侧面/背面并排的设定图）：路径 + 失败原因。
    # 它有两个作用：① 给用户一个"这个角色长这样"的确认物；
    # ② 支持参考图的厂商可以直接拿它当条件输入（见 ImageProvider.supports_reference）。
    turnaround_path: Mapped[str | None] = mapped_column(String(255), default=None)
    turnaround_error: Mapped[str | None] = mapped_column(Text, default=None)

    drama: Mapped[Drama] = relationship(back_populates="characters")
    lines: Mapped[list["DialogueLine"]] = relationship(back_populates="character")


class Episode(TimestampMixin, Base):
    __tablename__ = "episodes"
    # 同一部剧里集数不能重复 —— 数据库层兜底，比只在代码里判断可靠
    __table_args__ = (UniqueConstraint("drama_id", "number", name="uq_episode_drama_number"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    drama_id: Mapped[int] = mapped_column(ForeignKey("dramas.id", ondelete="CASCADE"), index=True)
    number: Mapped[int] = mapped_column(nullable=False)
    title: Mapped[str] = mapped_column(String(120), default="", nullable=False)
    synopsis: Mapped[str] = mapped_column(Text, default="", nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="draft", nullable=False)
    # 合成好的成片：路径 + 失败原因（一集一个视频）
    video_path: Mapped[str | None] = mapped_column(String(255), default=None)
    video_error: Mapped[str | None] = mapped_column(Text, default=None)

    drama: Mapped[Drama] = relationship(back_populates="episodes")
    shots: Mapped[list["Shot"]] = relationship(
        back_populates="episode",
        cascade="all, delete-orphan",
        order_by="Shot.number",
    )


class Shot(TimestampMixin, Base):
    """一个镜头 = 分镜脚本的最小单位，也是后续"文生图/文生视频"的输入。"""

    __tablename__ = "shots"

    id: Mapped[int] = mapped_column(primary_key=True)
    episode_id: Mapped[int] = mapped_column(ForeignKey("episodes.id", ondelete="CASCADE"), index=True)
    number: Mapped[int] = mapped_column(nullable=False)
    shot_type: Mapped[str] = mapped_column(String(30), default="中景", nullable=False)  # 景别
    camera: Mapped[str] = mapped_column(String(60), default="", nullable=False)  # 运镜
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)  # 画面描述
    dialogue: Mapped[str] = mapped_column(Text, default="", nullable=False)  # 台词/旁白
    duration_sec: Mapped[float] = mapped_column(default=3.0, nullable=False)
    image_prompt: Mapped[str] = mapped_column(Text, default="", nullable=False)  # 给文生图模型的提示词
    audio_prompt: Mapped[str] = mapped_column(Text, default="", nullable=False)  # 给配音(TTS)的提示词：语气/语速/情绪
    # 已经生成好的画面：存的是相对 media_root 的路径（文件在磁盘上，不入库）
    image_path: Mapped[str | None] = mapped_column(String(255), default=None)
    # 生成失败时的原因，成功则为 None —— 单张图失败不该让整个任务报废
    image_error: Mapped[str | None] = mapped_column(Text, default=None)
    # 配音同理：路径 + 失败原因（没有台词镜头会一直为空，前端显示"无需配音"）
    audio_path: Mapped[str | None] = mapped_column(String(255), default=None)
    audio_error: Mapped[str | None] = mapped_column(Text, default=None)
    # 动态片段（图生视频）：把这张静图变成一段真的会动的短片。
    # 为空时合成环节会退回用静态图 —— 也就是"没接图生视频也能出片"，向后兼容。
    clip_path: Mapped[str | None] = mapped_column(String(255), default=None)
    clip_error: Mapped[str | None] = mapped_column(Text, default=None)

    episode: Mapped[Episode] = relationship(back_populates="shots")
    # 这个镜头里的台词，一句一条。
    # 为什么要拆成独立表，而不是直接用 Shot.dialogue 那段文本：
    #   ① 一个镜头可能有多个人说话（`陈默：…` / `林晚：…`），
    #      每一句要用**各自角色的音色和语速**合成，逐句的音频和状态得分别记录；
    #   ② 情感分析是按"句"给结果的（这句是急迫、那句是暧昧），文本字段塞不下；
    #   ③ 某一句合成失败时，其它句还能保留，重试只需要补那一句。
    lines: Mapped[list["DialogueLine"]] = relationship(
        back_populates="shot",
        cascade="all, delete-orphan",
        order_by="DialogueLine.index",
    )


class DialogueLine(TimestampMixin, Base):
    """镜头里的一句台词（谁说的、说什么、什么情绪、合成到哪一步了）。"""

    __tablename__ = "dialogue_lines"

    id: Mapped[int] = mapped_column(primary_key=True)
    shot_id: Mapped[int] = mapped_column(ForeignKey("shots.id", ondelete="CASCADE"), index=True)
    index: Mapped[int] = mapped_column(default=0, nullable=False)  # 在镜头内的顺序（从 0 开始）
    speaker: Mapped[str] = mapped_column(String(60), default="", nullable=False)  # 原始说话人写法
    character_id: Mapped[int | None] = mapped_column(
        ForeignKey("characters.id", ondelete="SET NULL"), default=None, index=True
    )
    text: Mapped[str] = mapped_column(Text, default="", nullable=False)  # **只含要朗读的内容**
    cue: Mapped[str] = mapped_column(String(60), default="", nullable=False)  # 括号提示：内心独白/来电显示…
    emotion: Mapped[str] = mapped_column(String(20), default="", nullable=False)  # AI 判断的情绪
    intensity: Mapped[float] = mapped_column(Float, default=0.5, nullable=False)  # 情绪强度 0~1
    # 最终语速（角色基础语速 × 情绪修正）。
    # **为 None 表示还没做情绪分析**，此时用角色自己的基础语速 ——
    # 不能用 1.0 当默认值：1.0 是真值，会把角色语速（0.88/1.04 之类）永久盖掉。
    speed: Mapped[float | None] = mapped_column(Float, default=None)
    audio_path: Mapped[str | None] = mapped_column(String(255), default=None)  # 这一句的音频
    audio_error: Mapped[str | None] = mapped_column(Text, default=None)

    shot: Mapped[Shot] = relationship(back_populates="lines")
    character: Mapped[Character | None] = relationship(back_populates="lines")


class GenerationLog(TimestampMixin, Base):
    """每次 AI 生成调用记一条流水：配额统计和成本审计都靠它。

    为什么 AI 应用必须记调用流水：
    传统应用的"刷接口"最多浪费你一点 CPU；AI 应用的每次调用都是真金白银。
    没有流水，月底看到账单时根本说不清钱花在哪、谁花的。
    """

    __tablename__ = "generation_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    episode_id: Mapped[int | None] = mapped_column(
        ForeignKey("episodes.id", ondelete="CASCADE"), index=True, nullable=True
    )
    # 允许为空的原因：用户可以在"剧集还不存在"时先让 AI 起草大纲，
    # 这条调用流水此刻还没有对应的剧集。
    # 这次调用是干什么的：shots=生成分镜，synopsis=生成大纲。
    # 分开记才能在月底算清"钱花在哪个环节"。
    kind: Mapped[str] = mapped_column(String(20), default="shots", nullable=False)
    provider: Mapped[str] = mapped_column(String(40), default="", nullable=False)  # 用了哪家模型
    status: Mapped[str] = mapped_column(String(20), default="pending", nullable=False)  # pending/success/failed


class GenerationTask(TimestampMixin, Base):
    """一次异步生成任务。

    为什么生成必须异步（面试高频）：
    同步做法里，用户点"生成"后 HTTP 连接要挂着等大模型几十秒 ——
    期间占着一条连接、一个线程，浏览器还可能超时，用户也不知道进度。
    异步做法：接口立即返回 202 和任务 id，实际生成在后台跑，
    前端拿着任务 id 轮询 /tasks/{id} 看进度。这就是"任务队列"思想的最简形态。
    """

    __tablename__ = "generation_tasks"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    # 任务有两种粒度：
    #   集级（分镜/画面/动态片段/配音/成片）-> episode_id 有值
    #   剧级（角色三视图，它不属于任何一集）  -> drama_id 有值、episode_id 为空
    # 所以 episode_id 必须**可为空**。这个"同一张表装两种粒度"的做法是有代价的
    # （查询时要记得两种都考虑），但比再开一张任务表更简单 —— 状态机和轮询逻辑只写一份。
    episode_id: Mapped[int | None] = mapped_column(
        ForeignKey("episodes.id", ondelete="CASCADE"), default=None, index=True
    )
    drama_id: Mapped[int | None] = mapped_column(
        ForeignKey("dramas.id", ondelete="CASCADE"), default=None, index=True
    )
    # 任务类型：shots=生成分镜文本，images=生成每个镜头的画面，
    # clips=生成动态片段，audio=配音，video=合成，turnaround=角色三视图
    # 同一张任务表支持多种任务，前端用同一套轮询逻辑处理
    kind: Mapped[str] = mapped_column(String(20), default="shots", nullable=False)
    # pending -> running -> succeeded / failed
    status: Mapped[str] = mapped_column(String(20), default="pending", nullable=False)
    # 进度：本次任务总共要处理多少项、完成了多少项（生成 6 张图时前端可以显示 3/6）
    total: Mapped[int] = mapped_column(default=0, nullable=False)
    done: Mapped[int] = mapped_column(default=0, nullable=False)
    provider: Mapped[str] = mapped_column(String(40), default="", nullable=False)
    shot_count: Mapped[int] = mapped_column(default=6, nullable=False)
    extra_hint: Mapped[str] = mapped_column(Text, default="", nullable=False)
    error: Mapped[str | None] = mapped_column(Text, default=None)  # 失败原因，成功时为 None
    started_at: Mapped[datetime | None] = mapped_column(DateTime, default=None)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, default=None)
