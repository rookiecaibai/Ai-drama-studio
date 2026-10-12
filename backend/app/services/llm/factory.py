"""根据配置装配具体的 Provider（工厂模式）。

好处：想加一个新模型厂商，只需要写一个新类 + 在这里加一行分支，
业务代码和接口代码完全不用动 —— 这就是"对扩展开放、对修改关闭"。
"""

from app.core.config import Settings
from app.core.errors import AppError
from app.services.llm.base import LLMProvider
from app.services.llm.mock import MockProvider
from app.services.llm.openai_compat import OpenAICompatProvider


def build_provider(settings: Settings) -> LLMProvider:
    provider = settings.llm_provider.lower()

    if provider == "mock":
        return MockProvider()

    if provider == "openai_compat":
        return OpenAICompatProvider(
            api_key=settings.llm_api_key,
            base_url=settings.llm_base_url,
            model=settings.llm_model,
            timeout=settings.llm_timeout_sec,
        )

    raise AppError(
        f"未知的 LLM_PROVIDER: {settings.llm_provider}（可选 mock / openai_compat）",
        code="bad_provider_config",
        status_code=500,
    )
