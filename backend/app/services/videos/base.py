"""图生视频的抽象接口。

**它解决什么问题**
在这之前，成片是"每张图定格 + 配音"的幻灯片：画面完全静止，只有声音在动。
i2v（image-to-video）就是"给一张静图 + 一句运动描述，生成一段真的会动的短片"。

**为什么接口收的是图片路径而不是字节**
实现里要做两件截然不同的事：
- mock：调 ffmpeg 对这张图做运镜（需要文件路径）
- 真实厂商：把图片转成 base64 塞进请求（也是从文件读）
所以传路径最自然，两边都不需要额外落盘。

**返回字节而不是 URL**：和文生图同一个理由 —— 厂商给的视频链接是临时地址，
迟早失效，必须立刻下载存到自己磁盘上。
"""

from abc import ABC, abstractmethod
from pathlib import Path


class VideoClipProvider(ABC):
    name: str = "base"

    @abstractmethod
    async def generate(self, *, image_path: Path, prompt: str) -> bytes:
        """根据一张静态图 + 运动提示词，生成一段会动的视频（MP4 字节）。"""
        raise NotImplementedError
