"""FastAPI 依赖（Dependency Injection）。

依赖注入解决两个问题：
1. 路由函数里不再出现 `SessionLocal()`、`build_provider()` 这类"手工装配"代码；
2. 测试时可以整体替换（app.dependency_overrides），换成测试用数据库或假模型 ——
   这就是为什么上面的测试能在不碰真实数据库的情况下跑。
"""

from functools import lru_cache
from typing import Annotated

from fastapi import Depends
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings
from app.core.errors import AppError
from app.core.ratelimit import LoginLimiter
from app.core.security import decode_access_token
from app.db.models import User
from app.db.session import get_db, get_session_factory
from app.services.images.base import ImageProvider
from app.services.images.factory import build_image_provider
from app.services.llm.base import LLMProvider
from app.services.llm.factory import build_provider
from app.services.quota import QuotaService
from app.services.tts.base import TtsProvider
from app.services.tts.factory import build_tts_provider

DbSession = Annotated[Session, Depends(get_db)]

# OAuth2 "密码模式"：客户端拿用户名密码换 token。
# tokenUrl 指向登录接口 —— Swagger 文档右上角的 Authorize 按钮就会走这个流程。
# auto_error=False：令牌缺失时不抛框架自带的异常，而是进入 get_current_user
# 里抛我们自己定义的错误，保证所有错误都长成同一种 JSON 结构。
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login", auto_error=False)


def get_llm_provider() -> LLMProvider:
    """每次请求按当前配置构造 Provider（构造成本极低，且便于测试覆盖）。"""
    return build_provider(get_settings())


LlmProvider = Annotated[LLMProvider, Depends(get_llm_provider)]


def get_quota_service() -> QuotaService:
    """按配置构造配额服务。

    三种用途分别设限：生成分镜 / 生成大纲 用 DAILY_GENERATION_LIMIT，
    生成画面用 DAILY_IMAGE_LIMIT（图片贵得多）。
    写成依赖是为了测试时能换成"上限=2"的实例。
    """
    settings = get_settings()
    return QuotaService(
        limits={
            "shots": settings.daily_generation_limit,
            "synopsis": settings.daily_generation_limit,
            "images": settings.daily_image_limit,
            "audio": settings.daily_audio_limit,
            "video": settings.daily_video_limit,
        }
    )


QuotaServiceDep = Annotated[QuotaService, Depends(get_quota_service)]


def get_image_provider() -> ImageProvider:
    """文生图 Provider（和文本 Provider 完全同构的抽象）。"""
    return build_image_provider(get_settings())


ImageProviderDep = Annotated[ImageProvider, Depends(get_image_provider)]


def get_tts_provider() -> TtsProvider:
    """配音 Provider（同一套抽象，第三种模态）。"""
    return build_tts_provider(get_settings())


TtsProviderDep = Annotated[TtsProvider, Depends(get_tts_provider)]


@lru_cache(maxsize=1)
def get_login_limiter() -> LoginLimiter:
    """登录防爆破限流器。

    必须是进程级单例（lru_cache）：限流器的失败计数存在内存里，
    如果每个请求新建一个实例，计数永远攒不起来，限流就形同虚设 ——
    这个 bug 正是被测试 test_login_rate_limit_blocks_brute_force 抓出来的。
    """
    settings = get_settings()
    return LoginLimiter(max_attempts=settings.login_max_attempts, window_sec=settings.login_window_sec)


LoginLimiterDep = Annotated[LoginLimiter, Depends(get_login_limiter)]


def get_session_factory_dep() -> sessionmaker[Session]:
    """把会话工厂暴露成依赖。

    后台任务不能用请求的会话（请求结束后会话就关了），需要自己开新会话。
    把它做成依赖，测试里就能整体替换成测试数据库的会话工厂。
    """
    return get_session_factory()


SessionFactoryDep = Annotated[sessionmaker[Session], Depends(get_session_factory_dep)]


def get_current_user(
    db: DbSession,
    token: Annotated[str | None, Depends(oauth2_scheme)],
) -> User:
    """从请求头 Authorization: Bearer <token> 解析出当前登录用户。

    任何一个接口只要在参数里声明 CurrentUser，就自动获得鉴权能力：
    未登录 / 令牌无效 / 用户不存在，全部在这里被拦下（401）。
    """
    if not token:
        raise AppError("未登录：请先调用 /auth/login 获取令牌", code="not_authenticated", status_code=401)

    user_id = decode_access_token(token)
    user = db.get(User, user_id)
    if user is None or not user.is_active:
        raise AppError("用户不存在或已被停用", code="invalid_token", status_code=401)
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]
