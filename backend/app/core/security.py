"""安全相关：密码哈希与 JWT 令牌。

两个面试高频知识点：

1. 密码为什么不能明文存数据库？
   数据库一旦泄露（备份文件、SQL 注入、内部人员），攻击者直接拿到所有用户
   的明文密码 —— 而人们习惯到处复用密码，等于同时泄露了其他网站。
   存"哈希"意味着即使数据泄露，攻击者也只能拿到一串不可逆的摘要。

2. 为什么用 bcrypt 而不是 md5/sha256？
   - md5/sha256 是"通用哈希"，设计目标就是快 —— 快对攻击者是好事，
     暴力破解可以每秒尝试上亿次；
   - bcrypt 是"慢哈希"，每次验证故意花约 100ms 的计算量，把暴力破解的
     成本抬高几个数量级；
   - bcrypt 自动带"盐"（salt）：同一个密码两次哈希的结果都不同，
     攻击者没法用预先算好的彩虹表。
"""

import logging
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt

from app.core.config import get_settings
from app.core.errors import AppError

logger = logging.getLogger(__name__)

# bcrypt 只处理 72 字节以内的密码。一个中文字符在 UTF-8 里占 3 字节，
# 所以"72 个字符"的中文密码实际是 216 字节，会超出 —— 必须按字节数拦截。
_BCRYPT_MAX_BYTES = 72


def hash_password(plain: str) -> str:
    if len(plain.encode("utf-8")) > _BCRYPT_MAX_BYTES:
        raise AppError(
            "密码过长：bcrypt 最多支持 72 字节（一个中文字符占 3 字节）",
            code="password_too_long",
            status_code=422,
        )
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except ValueError:
        # 库里存的哈希格式损坏时返回 False，而不是抛 500
        return False


def create_access_token(user_id: int) -> str:
    """签发 JWT。

    JWT 的结构是 `header.payload.signature` 三段 Base64。
    - header：声明签名算法
    - payload：真正的内容（sub=用户id、exp=过期时间）
    - signature：用密钥对前两段签名，防止内容被篡改

    关键点：客户端可以"看见"payload（它只是 Base64 编码，不是加密），
    但**改不了**—— 改动之后签名就对不上，服务端会拒绝。
    所以"不要把敏感信息放进 payload"。
    """
    settings = get_settings()
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user_id),  # subject：这条令牌代表谁
        "iat": now,  # issued at：签发时间
        "exp": now + timedelta(minutes=settings.jwt_expire_minutes),  # 过期时间
    }
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str) -> int:
    """校验 JWT 并取出用户 id。任何不合法（过期/篡改/算法不符）都返回 401。"""
    settings = get_settings()
    try:
        payload = jwt.decode(token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])
    except jwt.PyJWTError as exc:
        raise AppError("登录凭证无效或已过期", code="invalid_token", status_code=401) from exc

    try:
        return int(payload["sub"])
    except (KeyError, TypeError, ValueError) as exc:
        raise AppError("登录凭证内容不合法", code="invalid_token", status_code=401) from exc
