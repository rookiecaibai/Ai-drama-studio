"""本地"图生视频"：用 ffmpeg 做运镜，产出**真的会动**的视频，不联网不花钱。

它和文本/图片/配音的 Mock 一样，价值在于：
1. 没有视频模型 API Key 时，也能把「动态片段 → 合成成片」整条链路跑通、测通；
2. 让测试能断言"画面确实动了"，而不用依赖外部服务（外部服务慢、贵、还会限流）。

和别的 Mock 不同的是：**它产出的不是占位符，而是一段真实可播放、真会动的视频**，
只是"动"的方式是运镜（推拉摇移），而不是人物真的做动作。
"""

import hashlib
import tempfile
from pathlib import Path

from app.services.videos.base import VideoClipProvider
from app.services.videos.kenburns import render_ken_burns

#: 镜头 id 从图片文件名里取（shots/12.jpg -> 12），用来决定运镜方式
def _variant_from(image_path: Path) -> int:
    digest = hashlib.sha256(image_path.name.encode("utf-8")).digest()
    return digest[0] % 4


class MockVideoClipProvider(VideoClipProvider):
    name = "mock"

    def __init__(self, *, width: int = 720, height: int = 1280, fps: int = 25, seconds: float = 4.0):
        self._width = width
        self._height = height
        self._fps = fps
        self._seconds = seconds

    async def generate(self, *, image_path: Path, prompt: str) -> bytes:
        variant = _variant_from(image_path)
        with tempfile.TemporaryDirectory(prefix="drama-clip-") as work_dir:
            out_path = Path(work_dir) / "clip.mp4"
            render_ken_burns(
                image_path,
                out_path,
                seconds=self._seconds,
                width=self._width,
                height=self._height,
                fps=self._fps,
                variant=variant,
            )
            return out_path.read_bytes()
