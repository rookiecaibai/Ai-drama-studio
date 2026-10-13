"""每日调用配额：防止大模型调用费用被刷爆。

为什么这个功能对 AI 应用是必修课：
传统 Web 应用被"刷接口"，顶多浪费你一点 CPU 和带宽；
AI 应用被刷接口，是直接刷你的钱包 —— 每次调用大模型都是按量计费。
所以"配额 + 调用流水"不是锦上添花，是成本底线。

为什么按用途分别设限（limits 字典）：
生成一段文字几分钱，生成一张图几分到几毛。如果共用一个额度，
要么图片把文本额度吃光，要么文本把图片额度吃光 —— 两种都很难受。
"""

import logging
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.db.models import GenerationLog

logger = logging.getLogger(__name__)

# 给用户看的名称，让 429 的错误信息说人话
# （漏了哪个 kind，用户就会看到"今日「调用」次数已达上限"这种含糊提示 ——
#   这个遗漏正是被 test_audio_quota_is_separate 抓出来的）
_KIND_LABEL = {
    "shots": "生成分镜",
    "synopsis": "生成大纲",
    "images": "生成画面",
    "audio": "生成配音",
    "video": "合成视频",
}


class QuotaService:
    """按用户统计"今天"的调用次数，超限则拒绝，并记录每次调用。

    用法：
        QuotaService(limits={"shots": 20, "synopsis": 20, "images": 30})
        QuotaService(daily_limit=2)   # 兼容写法：所有用途共用同一个上限
    """

    def __init__(self, limits: dict[str, int] | None = None, *, daily_limit: int | None = None):
        if limits is None:
            shared = daily_limit if daily_limit is not None else 0
            limits = {"shots": shared, "synopsis": shared, "images": shared}
        self.limits = limits

    def ensure_allowed(self, db: Session, user_id: int, kind: str = "shots") -> None:
        """检查今天这个用途是否还有额度。超限抛 429。"""
        limit = self.limits.get(kind, 0)
        if limit <= 0:  # <=0 表示不限制
            return

        # 注意：created_at 是数据库端 server_default=now() 生成的（naive UTC），
        # 所以边界也取 naive UTC，避免时区类型不一致导致比较永远为空
        today_start = datetime.now(timezone.utc).replace(
            hour=0, minute=0, second=0, microsecond=0
        ).replace(tzinfo=None)

        used = (
            db.scalar(
                select(func.count())
                .select_from(GenerationLog)
                .where(
                    GenerationLog.user_id == user_id,
                    GenerationLog.kind == kind,
                    GenerationLog.created_at >= today_start,
                )
            )
            or 0
        )

        if used >= limit:
            label = _KIND_LABEL.get(kind, "调用")
            raise AppError(
                f"今日「{label}」次数已达上限（{limit} 次），明天再来吧",
                code="quota_exceeded",
                status_code=429,
            )

    def record(
        self,
        db: Session,
        *,
        user_id: int,
        episode_id: int | None,
        provider: str,
        kind: str = "shots",
    ) -> GenerationLog:
        """在真正调用大模型之前就记一条 pending 流水。

        kind: shots=生成分镜，synopsis=生成大纲，images=生成画面。
        分开记才能在月底算清"钱花在哪个环节"。

        为什么在调用"前"记而不是调用"后"记：
        如果只记成功的那部分，攻击者可以反复触发失败请求 ——
        失败请求一样要付钱给模型厂商。先占额度，失败也消耗，
        才能防止"靠重试刷你"。
        """
        log = GenerationLog(
            user_id=user_id,
            episode_id=episode_id,
            provider=provider,
            kind=kind,
            status="pending",
        )
        db.add(log)
        db.commit()
        db.refresh(log)
        return log

    @staticmethod
    def finish(db: Session, log: GenerationLog, status: str) -> None:
        """调用结束后把流水标记为 success/failed。"""
        log.status = status
        db.commit()
