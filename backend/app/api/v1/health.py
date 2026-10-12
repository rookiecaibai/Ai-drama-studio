"""健康检查接口。

为什么需要它：
- 容器编排（Docker/K8s）靠它判断实例是否可用，不可用就重启或摘流量；
- 上线后第一件事就是"服务活着吗、数据库通吗"，有这个接口就不用登录服务器猜。
"""

from fastapi import APIRouter
from sqlalchemy import text

from app.api.deps import DbSession
from app.core.config import get_settings

router = APIRouter(prefix="/health", tags=["health"])


@router.get("", summary="健康检查（含数据库连通性）")
def health(db: DbSession) -> dict:
    settings = get_settings()
    db.execute(text("SELECT 1"))  # 数据库不通会抛异常，返回 500，而不是假的 ok
    return {
        "status": "ok",
        "app": settings.app_name,
        "env": settings.env,
        "llm_provider": settings.llm_provider,
    }
