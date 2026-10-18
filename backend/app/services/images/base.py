"""图片生成（文生图）的抽象接口。

和文本模型一样，业务代码只认这个接口：
- MockImageProvider：本地画一张占位图，把提示词写在画面上（不联网、不花钱）
- OpenAICompatImageProvider：真的调用厂商接口

为什么返回值是 bytes（图片字节）而不是 URL：
厂商给的图片 URL 通常是**临时地址**（几分钟到几小时就过期），
直接存 URL 的话，用户过两天回来就看不到图了。
所以我们的做法是：拿到图立刻下载下来存到自己的磁盘，URL 用完即弃。

**关于"参考图"（reference_image）**：
有的厂商支持拿一张图当条件（image-to-image / 角色参考），有的只吃文字。
智谱的 cogview-3-flash / cogview-4 / glm-image **都是纯文本输入**（官方文档的
"输入模态"一栏写的是文本），所以真实适配器的 supports_reference 是 False。

我们仍然把这个通道留在接口里，理由是：
① 让"不支持"变成**显式的能力标志**，而不是悄悄丢参数造成误解；
② 业务层能据此正确地降级（退回提示词描述）并记日志；
③ 将来换成支持参考图的厂商（或厂商升级），只需要实现里处理一下，业务代码不动。
"""

from abc import ABC, abstractmethod
from pathlib import Path


class ImageProvider(ABC):
    name: str = "base"

    #: 是否支持"参考图"（image-to-image / 角色参考）。
    supports_reference: bool = False

    @abstractmethod
    async def generate(self, *, prompt: str, reference_image: Path | None = None) -> bytes:
        """根据提示词生成一张图片，返回图片的原始字节（PNG/JPEG）。

        reference_image：可选的参考图（例如角色的三视图）。
        只有 supports_reference=True 的实现才会真正使用它；其余实现应忽略它。
        """
        raise NotImplementedError
