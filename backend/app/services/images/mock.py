"""本地占位图：不联网、不花钱，把提示词直接画在画面上。

它的价值和文本侧的 MockProvider 一样：
1. 没有图片生成的 API Key 时，也能把"提示词 -> 图片 -> 存盘 -> 带鉴权返回给前端"
   整条链路调通；
2. 让测试不依赖外部服务、不花钱、结果可复现。

两个刻意的设计：

**① 背景色由提示词的哈希决定** —— 同一个提示词永远得到同一种颜色，
这样测试可以断言"同样的输入产生同样的图"，演示时也能一眼看出各镜头是不同内容。

**② 画布尺寸跟着 IMAGE_SIZE 配置走** —— 这条很重要：
如果 mock 固定输出正方形、真实厂商输出竖屏，那么"图片放进竖屏成片会怎样"
在 mock 模式下就演示不出来（黑边问题要等接上真图才发现）。
让 mock 和真实产出**版式一致**，mock 阶段的观感才是有参考价值的。
"""

import hashlib
import re
from io import BytesIO

from PIL import Image, ImageDraw, ImageFont

from app.core.config import get_settings
from app.services.images.base import ImageProvider

FALLBACK_SIZE = (768, 1344)


def _canvas_size() -> tuple[int, int]:
    """从 IMAGE_SIZE（形如 768x1344）解析画布尺寸。"""
    raw = (get_settings().image_size or "").lower().replace(" ", "")
    match = re.fullmatch(r"(\d{2,4})x(\d{2,4})", raw)
    if not match:
        return FALLBACK_SIZE
    width, height = int(match.group(1)), int(match.group(2))
    # 夹在合理范围：配置写错时不至于生成一张几个亿像素的图把内存吃光
    return (max(256, min(2048, width)), max(256, min(2048, height)))


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
        width, height = _canvas_size()
        digest = hashlib.sha256(prompt.encode("utf-8")).digest()
        background = tuple(20 + digest[i] % 70 for i in range(3))

        image = Image.new("RGB", (width, height), background)
        draw = ImageDraw.Draw(image)

        # 字号跟着画布宽度走，保证不同尺寸下版式比例一致
        title_size = max(13, width // 42)
        body_size = max(14, width // 34)
        small_size = max(11, width // 56)
        title_font = ImageFont.load_default(size=title_size)
        body_font = ImageFont.load_default(size=body_size)
        small_font = ImageFont.load_default(size=small_size)

        margin = max(10, width // 28)
        draw.rectangle(
            [margin // 2, margin // 2, width - margin // 2 - 1, height - margin // 2 - 1],
            outline=(210, 218, 226),
            width=2,
        )

        y = margin
        for line in _wrap("MOCK PLACEHOLDER - no real image model was called", width, 2):
            draw.text((margin, y), line, fill=(255, 200, 130), font=title_font)
            y += title_size + 6

        y += body_size
        chars_per_line = max(12, int((width - margin * 2) / (body_size * 0.62)))
        for line in _wrap(prompt, chars_per_line, 14):
            draw.text((margin, y), line, fill=(238, 242, 246), font=body_font)
            y += body_size + 6

        draw.text(
            (margin, height - margin - small_size - 4),
            f"size={width}x{height}  prompt sha256={digest.hex()[:12]}",
            fill=(170, 180, 190),
            font=small_font,
        )

        buffer = BytesIO()
        image.save(buffer, format="PNG")
        return buffer.getvalue()
