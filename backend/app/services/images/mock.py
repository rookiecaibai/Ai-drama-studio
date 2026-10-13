"""本地占位图：不联网、不花钱，把提示词直接画在画面上。

它的价值和文本侧的 MockProvider 一样：
1. 没有图片生成的 API Key 时，也能把"提示词 -> 图片 -> 存盘 -> 带鉴权返回给前端"
   整条链路调通；
2. 让测试不依赖外部服务、不花钱、结果可复现。

设计细节：背景色由提示词的哈希决定 —— 同一个提示词永远得到同一种颜色，
这样测试可以断言"同样的输入产生同样的图"，也让演示时看起来每个镜头确实不一样。
"""

import hashlib
from io import BytesIO

from PIL import Image, ImageDraw, ImageFont

from app.services.images.base import ImageProvider

WIDTH, HEIGHT = 640, 360


def _wrap(text: str, width: int, max_lines: int) -> list[str]:
    """按字符切行。提示词是英文，用默认字体就能画出来。"""
    lines: list[str] = []
    for paragraph in text.splitlines() or [""]:
        paragraph = paragraph.strip()
        while paragraph and len(lines) < max_lines:
            lines.append(paragraph[:width])
            paragraph = paragraph[width:]
    return lines or ["(empty prompt)"]


class MockImageProvider(ImageProvider):
    name = "mock"

    async def generate(self, *, prompt: str) -> bytes:
        digest = hashlib.sha256(prompt.encode("utf-8")).digest()
        background = tuple(20 + digest[i] % 70 for i in range(3))

        image = Image.new("RGB", (WIDTH, HEIGHT), background)
        draw = ImageDraw.Draw(image)
        font = ImageFont.load_default(size=16)
        small = ImageFont.load_default(size=13)

        draw.rectangle([6, 6, WIDTH - 7, HEIGHT - 7], outline=(210, 218, 226), width=2)
        draw.text((20, 18), "MOCK PLACEHOLDER — no real image model was called", fill=(255, 200, 130), font=small)

        y = 60
        for line in _wrap(prompt, 52, 12):
            draw.text((20, y), line, fill=(238, 242, 246), font=font)
            y += 22

        draw.text(
            (20, HEIGHT - 34),
            f"prompt sha256={digest.hex()[:12]}",
            fill=(170, 180, 190),
            font=small,
        )

        buffer = BytesIO()
        image.save(buffer, format="PNG")
        return buffer.getvalue()
