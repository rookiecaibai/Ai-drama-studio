"""本地配音：合成一段**真的能播放**的 WAV 提示音。

为什么不用空文件糊弄：
1. 空文件无法验证"音频能不能被播放器识别"这件事 —— 而这正是配音链路最容易出问题的地方
   （Content-Type 给错、扩展名不对、采样率不合法，都会让播放器直接拒绝）；
2. 视频合成时要用真实音频时长来对齐画面，音频时长为 0 会让整条流水线的测试失去意义。

用标准库 wave + struct 生成，不引入任何依赖。
"""

import hashlib
import math
import struct
import wave
from io import BytesIO

from app.services.tts.base import TtsProvider

SAMPLE_RATE = 22050
MAX_SECONDS = 20.0


def _duration_for(text: str) -> float:
    """按文字长度估一个合理的时长（中文约每字 0.15 秒），并夹在合理区间内。"""
    estimate = len(text.strip()) * 0.15
    return max(0.8, min(MAX_SECONDS, estimate))


def _frequency_for(text: str) -> float:
    """不同台词给不同音高 —— 演示时听起来"每个镜头不一样"，也便于区分文件。"""
    digest = hashlib.sha256(text.encode("utf-8")).digest()
    return 180.0 + digest[0] % 12 * 20.0  # 180 ~ 400 Hz


def _render_wav(seconds: float, frequency: float) -> bytes:
    frames = int(SAMPLE_RATE * seconds)
    fade = max(1, int(SAMPLE_RATE * 0.03))  # 30ms 淡入淡出，避免开头结尾"啪"的爆音

    samples = bytearray()
    for index in range(frames):
        # 三角包络：0 -> 1 -> 0，避免爆音
        if index < fade:
            envelope = index / fade
        elif index > frames - fade:
            envelope = max(0.0, (frames - index) / fade)
        else:
            envelope = 1.0
        # 叠加一个高八度的弱音，听起来不那么"电子"
        value = math.sin(2 * math.pi * frequency * index / SAMPLE_RATE)
        value += 0.3 * math.sin(4 * math.pi * frequency * index / SAMPLE_RATE)
        samples += struct.pack("<h", int(9000 * envelope * value))

    buffer = BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(SAMPLE_RATE)
        handle.writeframes(bytes(samples))
    return buffer.getvalue()


class MockTtsProvider(TtsProvider):
    name = "mock"
    extension = "wav"

    async def synthesize(
        self,
        *,
        text: str,
        style: str = "",
        voice: str | None = None,
        speed: float | None = None,
    ) -> bytes:
        if not text.strip():
            raise ValueError("没有可配音的文字")
        # 语速也要在 mock 里体现出来：否则"急迫 -> 调快语速"这条逻辑
        # 在 mock 阶段完全看不出来，等接上真厂商才发现问题。
        seconds = _duration_for(text) / max(0.1, speed or 1.0)
        # 把音色也算进音高：不同角色在 mock 下也能听出"不是同一个声音"
        frequency = _frequency_for(f"{voice or ''}|{text}|{style}")
        return _render_wav(seconds, frequency)
