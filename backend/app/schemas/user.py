"""用户与令牌 Schema。"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class UserCreate(BaseModel):
    # 用户名限定字母数字下划线：将来用户名会出现在 URL 或消息系统里，字符集越窄越省心
    username: str = Field(min_length=3, max_length=50, pattern=r"^[a-zA-Z0-9_]+$", description="登录名")
    password: str = Field(min_length=8, max_length=64, description="密码，至少 8 位")
    email: str | None = Field(default=None, max_length=120)


class UserRead(BaseModel):
    """返回给前端的用户信息。

    注意这里刻意没有 password_hash 字段 —— 这就是"Schema 与 ORM 模型分开写"
    的安全意义：数据库里存了哈希，但接口永远不可能把它泄露出去。
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    email: str | None
    created_at: datetime


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserRead
