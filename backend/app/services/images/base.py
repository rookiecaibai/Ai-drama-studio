"""图片生成（文生图）的抽象接口。

和文本模型一样，业务代码只认这个接口：
- MockImageProvider：本地画一张占位图，把提示词写在画面上（不联网、不花钱）
- OpenAICompatImageProvider：真的调用厂商接口

为什么返回值是 bytes（图片字节）而不是 URL：
厂商给的图片 URL 通常是**临时地址**（几分钟到几小时就过期），
直接存 URL 的话，用户过两天回来就看不到图了。
所以我们的做法是：拿到图立刻下载下来存到自己的磁盘，URL 用完即弃。
"""

from abc import ABC, abstractmethod


class ImageProvider(ABC):
    name: str = "base"

    @abstractmethod
    async def generate(self, *, prompt: str) -> bytes:
        """根据提示词生成一张图片，返回图片的原始字节（PNG/JPEG）。"""
        raise NotImplementedError
