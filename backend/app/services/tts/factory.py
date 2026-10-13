"""根据配置装配配音 Provider（和文本、图片侧完全同构）。"""

from app.core.config import Settings
from app.core.errors import AppError
from app.services.tts.base import TtsProvider
from app.services.tts.mock import MockTtsProvider
from app.services.tts.openai_compat import OpenAICompatTtsProvider


def build_tts_provider(settings: Settings) -> TtsProvider:
    provider = settings.tts_provider.lower()

    if provider == "mock":
        return MockTtsProvider()

    if provider == "openai_compat":
        return OpenAICompatTtsProvider(
            api_key=settings.tts_api_key,
            base_url=settings.tts_base_url,
            model=settings.tts_model,
            voice=settings.tts_voice,
            response_format=settings.tts_response_format,
            instructions_param=settings.tts_instructions_param,
            timeout=settings.tts_timeout_sec,
        )

    raise AppError(
        f"未知的 TTS_PROVIDER: {settings.tts_provider}（可选 mock / openai_compat）",
        code="bad_tts_provider_config",
        status_code=500,
    )
