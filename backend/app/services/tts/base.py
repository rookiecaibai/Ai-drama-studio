"""配音（TTS）的抽象接口。

和文本、图片一样：业务代码只认这个接口。
- MockTtsProvider：本地合成一段可播放的提示音（不联网、不花钱，用来调通链路）
- OpenAICompatTtsProvider：调用厂商的 /audio/speech 接口

返回值是音频字节。要注意的是**音频格式由厂商决定**（常见 mp3，也有 wav/opus），
所以调用方要从 MIME 或配置里拿到扩展名，不能假定它一定是 mp3。
"""

from abc import ABC, abstractmethod


class TtsProvider(ABC):
    name: str = "base"
    #: 产出音频的文件扩展名（决定存盘路径和 Content-Type）
    extension: str = "mp3"

    @abstractmethod
    async def synthesize(self, *, text: str, style: str = "") -> bytes:
        """把一段文字合成为语音。

        style 对应镜头里的 audio_prompt（语气/情绪提示），
        是否真的用得上取决于厂商是否支持 instructions 之类的参数。
        """
        raise NotImplementedError
