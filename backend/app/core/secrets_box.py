"""API Key 的加密存储。

**为什么必须加密，而不是"反正数据库不给别人看"**：
用户填进来的 Key 是**真金白银的凭证**，一旦泄露，别人能拿去刷他的额度。
而数据库泄露的途径比想象的多：备份文件、慢查询日志、开发同事本地的一份拷贝、
`mysqldump` 出来的 SQL、甚至 `docker compose logs` 里不小心打印的配置。
所以"密钥不能明文落库"是对用户最基本的负责。

**做法**：Fernet 对称加密（cryptography 库自带，AES-128-CBC + HMAC 完整性校验）。
密钥从 `JWT_SECRET_KEY` 派生 —— 不额外增加配置项，代价是：
**轮换 JWT_SECRET_KEY 之后，已存的 Key 会解不开，用户需要重新填一次**。
这个取舍对当前项目是合理的（否则就要引入 KMS/密钥管理服务）。

**注意**：加密只解决"落库"这一环。接口返回时我们**永远不返回明文**，
只返回 `has_key` 和掩码（如 `sk-…9f2`），前端也不需要回显它。
"""

import base64
import hashlib
import logging

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import get_settings

logger = logging.getLogger(__name__)

_PREFIX = "enc:v1:"


def _fernet() -> Fernet:
    """从 JWT_SECRET_KEY 派生出一个稳定的 Fernet 密钥。"""
    secret = get_settings().jwt_secret_key or "dev-only-secret"
    digest = hashlib.sha256(f"drama-api-key::{secret}".encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_key(plain: str) -> str:
    """加密一个 API Key；空字符串返回空字符串（表示"没填"）。"""
    plain = (plain or "").strip()
    if not plain:
        return ""
    return _PREFIX + _fernet().encrypt(plain.encode("utf-8")).decode("ascii")


def decrypt_key(stored: str) -> str:
    """解密。解不开时返回空串并记一条日志（而不是抛异常）。

    为什么容错：JWT_SECRET_KEY 换过之后旧密文就解不开了，
    这时候正确的行为是"当作没填、回退到服务端默认"，而不是让整个生成接口 500。
    """
    stored = (stored or "").strip()
    if not stored:
        return ""
    if not stored.startswith(_PREFIX):
        # 兼容早期可能直接存的明文（本项目没有，但迁移场景要能容错）
        return stored
    try:
        return _fernet().decrypt(stored[len(_PREFIX) :].encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError):
        logger.warning("API Key 解密失败（JWT_SECRET_KEY 可能已轮换），将回退到服务端默认配置")
        return ""


def mask_key(plain: str) -> str:
    """把 Key 变成给人看的掩码：`sk-abcd…9f2`。

    只露前 6 位和后 3 位 —— 足够让用户认出"这是哪一把 Key"，
    又不足以被拿去用。
    """
    plain = (plain or "").strip()
    if not plain:
        return ""
    if len(plain) <= 12:
        return "已配置"
    return f"{plain[:6]}…{plain[-3:]}"
