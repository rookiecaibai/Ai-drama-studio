"""根据配置装配图生视频 Provider。

注意 `none` 返回的是 **None**（而不是某个"什么都不做"的实现）：
"不生成动态片段"不是一个策略，而是"这条流水线不存在"。
用 None 表达更诚实 —— 调用方一看就知道要跳过，不必去猜某个空实现的行为。
"""

import logging

from app.core.config import Settings
from app.core.errors import AppError
from app.services.llm.params import parse_extra_params
from app.services.videos.base import VideoClipProvider
from app.services.videos.mock import MockVideoClipProvider
from app.services.videos.openai_compat import OpenAICompatVideoClipProvider

logger = logging.getLogger(__name__)


def build_clip_provider(settings: Settings) -> VideoClipProvider | None:
    provider = (settings.video_clip_provider or "none").lower()

    if provider == "none":
        return None

    if provider == "mock":
        return MockVideoClipProvider(
            width=settings.video_width,
            height=settings.video_height,
            fps=settings.video_fps,
            seconds=float(settings.video_clip_seconds),
        )

    if provider == "openai_compat":
        # 智谱的文生图和图生视频是同一个 Key —— 留空就复用，省得配两遍
        api_key = settings.video_api_key or settings.image_api_key
        if not settings.video_api_key and settings.image_api_key:
            logger.info("VIDEO_API_KEY 未配置，复用 IMAGE_API_KEY")
        return OpenAICompatVideoClipProvider(
            api_key=api_key,
            base_url=settings.video_base_url,
            model=settings.video_model,
            size=settings.video_size,
            quality=settings.video_quality,
            seconds=settings.video_clip_seconds,
            extra_params=parse_extra_params(settings.video_extra_params, name="VIDEO_EXTRA_PARAMS"),
            timeout=settings.video_timeout_sec,
            poll_interval=settings.video_poll_interval_sec,
            poll_timeout=settings.video_poll_timeout_sec,
        )

    raise AppError(
        f"未知的 VIDEO_CLIP_PROVIDER: {settings.video_clip_provider}（可选 none / mock / openai_compat）",
        code="bad_video_provider_config",
        status_code=500,
    )
