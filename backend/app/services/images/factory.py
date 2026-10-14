"""根据配置装配图片生成 Provider（和文本侧的 factory 完全同构）。"""

from app.core.config import Settings
from app.core.errors import AppError
from app.services.images.base import ImageProvider
from app.services.images.mock import MockImageProvider
from app.services.images.openai_compat import OpenAICompatImageProvider
from app.services.llm.params import parse_extra_params


def build_image_provider(settings: Settings) -> ImageProvider:
    provider = settings.image_provider.lower()

    if provider == "mock":
        return MockImageProvider()

    if provider == "openai_compat":
        return OpenAICompatImageProvider(
            api_key=settings.image_api_key,
            base_url=settings.image_base_url,
            model=settings.image_model,
            size=settings.image_size,
            size_param=settings.image_size_param,
            timeout=settings.image_timeout_sec,
            extra_params=parse_extra_params(
                settings.image_extra_params, name="IMAGE_EXTRA_PARAMS"
            ),
        )

    raise AppError(
        f"未知的 IMAGE_PROVIDER: {settings.image_provider}（可选 mock / openai_compat）",
        code="bad_image_provider_config",
        status_code=500,
    )
