"""FastAPI 依赖（Dependency Injection）。

依赖注入解决两个问题：
1. 路由函数里不再出现 `SessionLocal()`、`build_provider()` 这类"手工装配"代码；
2. 测试时可以整体替换（app.dependency_overrides），换成测试用数据库或假模型 ——
   这就是为什么上面的测试能在不碰真实数据库的情况下跑。
"""

from typing import Annotated

from fastapi import Depends
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.session import get_db
from app.services.llm.base import LLMProvider
from app.services.llm.factory import build_provider

DbSession = Annotated[Session, Depends(get_db)]


def get_llm_provider() -> LLMProvider:
    """每次请求按当前配置构造 Provider（构造成本极低，且便于测试覆盖）。"""
    return build_provider(get_settings())


LlmProvider = Annotated[LLMProvider, Depends(get_llm_provider)]
