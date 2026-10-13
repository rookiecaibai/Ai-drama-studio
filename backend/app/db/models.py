"""数据模型：短剧(Drama) -> 剧集(Episode) -> 镜头(Shot)。

为什么是三层而不是一张大表：
- 短剧是一个"作品"，会有多集；每集有独立标题/梗概/状态；每集又切分成若干镜头。
- 拆表后，生成脚本只需要动 Shot 表，删除短剧靠外键级联清理，语义清晰。
- 这也是短剧行业真实的业务结构，面试时能讲清"为什么这样建模"比"我用了 ORM"更值钱。
"""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text, UniqueConstraint, func
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

    episode: Mapped[Episode] = relationship(back_populates="shots")


class GenerationLog(TimestampMixin, Base):
    """每次 AI 生成调用记一条流水：配额统计和成本审计都靠它。

    为什么 AI 应用必须记调用流水：
    传统应用的"刷接口"最多浪费你一点 CPU；AI 应用的每次调用都是真金白银。
    没有流水，月底看到账单时根本说不清钱花在哪、谁花的。
    """

    __tablename__ = "generation_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    episode_id: Mapped[int] = mapped_column(ForeignKey("episodes.id", ondelete="CASCADE"), index=True)
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
    episode_id: Mapped[int] = mapped_column(ForeignKey("episodes.id", ondelete="CASCADE"), index=True)
    # pending -> running -> succeeded / failed
    status: Mapped[str] = mapped_column(String(20), default="pending", nullable=False)
    provider: Mapped[str] = mapped_column(String(40), default="", nullable=False)
    shot_count: Mapped[int] = mapped_column(default=6, nullable=False)
    extra_hint: Mapped[str] = mapped_column(Text, default="", nullable=False)
    error: Mapped[str | None] = mapped_column(Text, default=None)  # 失败原因，成功时为 None
    started_at: Mapped[datetime | None] = mapped_column(DateTime, default=None)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, default=None)
