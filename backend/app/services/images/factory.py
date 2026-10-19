"""根据配置装配图片生成 Provider（和文本侧的 factory 完全同构）。"""

from app.core.config import Settings
from app.core.errors import AppError
from app.services.images.base import ImageProvider
from app.services.images.mock import MockImageProvider
from app.services.images.openai_compat import OpenAICompatImageProvider
from app.services.images.wanx import WanxImageProvider
from app.services.llm.params import parse_extra_params


def build_image_provider(settings: Settings) -> ImageProvider:
    provider = settings.image_provider.lower()

    if provider == "mock":
        return MockImageProvider()

    if provider == "wanx":
        # 目前唯一支持参考图的渠道：角色一致性靠它，而不是靠文字描述
        return WanxImageProvider(
            api_key=settings.wanx_api_key or settings.image_api_key,
            base_url=settings.wanx_base_url,
            model=settings.wanx_model,
            # 显式传像素尺寸：有参考图时万相会跟随参考图的长宽比，
            # 不显式指定就会出一张比例奇怪的图，和成片的 768x1344 对不上
            size=settings.image_size or "1K",
            timeout=settings.image_timeout_sec,
        )

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
            # 节流 + 重试：把"自己把自己限流打满"这件事从根上避免（见 provider 里的说明）
            min_interval=settings.image_min_interval_sec,
            retry_attempts=settings.image_retry_attempts,
        )

    raise AppError(
        f"未知的 IMAGE_PROVIDER: {settings.image_provider}（可选 mock / openai_compat / wanx）",
        code="bad_image_provider_config",
        status_code=500,
    )
