"""注册 / 登录 / 查看当前用户。"""

from typing import Annotated

from fastapi import APIRouter, Depends, Request, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import select

from app.api.deps import CurrentUser, DbSession, LoginLimiterDep
from app.core.errors import AppError, ConflictError
from app.core.security import create_access_token, hash_password, verify_password
from app.db.models import User
from app.schemas.user import Token, UserCreate, UserRead

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", response_model=UserRead, status_code=status.HTTP_201_CREATED, summary="注册新用户")
def register(payload: UserCreate, db: DbSession) -> User:
    exists = db.scalar(select(User.id).where(User.username == payload.username))
    if exists is not None:
        raise ConflictError(f"用户名 {payload.username} 已被占用")

    user = User(
        username=payload.username,
        email=payload.email,
        password_hash=hash_password(payload.password),
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@router.post("/login", response_model=Token, summary="登录，获取访问令牌")
def login(
    form: Annotated[OAuth2PasswordRequestForm, Depends()],
    db: DbSession,
    request: Request,
    limiter: LoginLimiterDep,
) -> Token:
    """注意：这个接口收的是表单（username/password），不是 JSON。

    这是 OAuth2 规范规定的格式；Swagger 的 Authorize 按钮、
    以及各类 HTTP 客户端，都按这个格式发送。
    """
    # 限流的 key：用户名 + 客户端 IP。只看 IP 会误伤同 NAT/公司网络的人，
    # 只看用户名会让攻击者换着用户名试同一个密码。两个维度都算上。
    client_ip = request.client.host if request.client else "unknown"
    key = f"login:{form.username}@{client_ip}"

    if limiter.is_blocked(key):
        raise AppError("尝试次数过多，请稍后再试", code="too_many_attempts", status_code=429)

    # 登录失败统一返回"用户名或密码错误"，不区分具体是哪种：
    # 如果返回"用户名不存在"，攻击者就能枚举出哪些用户名已注册。
    user = db.scalar(select(User).where(User.username == form.username))
    if user is None or not verify_password(form.password, user.password_hash):
        limiter.record_failure(key)
        raise AppError("用户名或密码错误", code="bad_credentials", status_code=401)

    limiter.clear(key)  # 登录成功清零：不让正常用户被自己偶尔的手滑锁死
    return Token(
        access_token=create_access_token(user.id),
        user=UserRead.model_validate(user),
    )


@router.get("/me", response_model=UserRead, summary="查看当前登录用户")
def read_me(user: CurrentUser) -> User:
    """这个接口没有一行鉴权代码 —— 全在 CurrentUser 依赖里完成了。"""
    return user
