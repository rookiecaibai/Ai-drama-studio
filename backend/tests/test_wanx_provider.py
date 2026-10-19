"""万相适配器 + 三视图裁人像的测试。

守护两件在实测中直接踩到的事故：
    ① **整张三视图当参考图，模型会画成三个人** -> 所以必须先裁成单人像；
    ② **万相的尺寸格式是 `768*1344`（星号）**，传 `768x1344` 会 400。
"""

from pathlib import Path

from PIL import Image, ImageDraw

from app.services import media
from app.services.images.wanx import _extract_image_url, _normalize_size
from app.services.portrait import _content_strips, build_portrait


def test_size_is_converted_to_wanx_format():
    """万相要求 width*height；我们内部统一用 768x1344。"""
    assert _normalize_size("768x1344") == "768*1344"
    assert _normalize_size("1024X1024") == "1024*1024"
    # 档位写法（1K/2K/4K）原样保留
    assert _normalize_size("2K") == "2K"
    assert _normalize_size("") == "1K"


def test_extract_image_url_handles_both_shapes():
    new_shape = {"output": {"choices": [{"message": {"content": [{"image": "http://a/x.png"}]}}]}}
    old_shape = {"output": {"results": [{"url": "http://b/y.png"}]}}
    assert _extract_image_url(new_shape) == "http://a/x.png"
    assert _extract_image_url(old_shape) == "http://b/y.png"


def _draw_figures(count: int, width: int = 900, height: int = 1200) -> Image.Image:
    """造一张"白底 + N 个竖条人物"的假三视图。"""
    image = Image.new("RGB", (width, height), (255, 255, 255))
    draw = ImageDraw.Draw(image)
    slot = width // count
    for index in range(count):
        left = index * slot + slot // 4
        right = (index + 1) * slot - slot // 4
        draw.rectangle([left, height // 5, right, height * 4 // 5], fill=(40, 60, 90))
    return image


def test_detects_multiple_views():
    """三个视角并排时，应该识别出三块内容。"""
    assert len(_content_strips(_draw_figures(3))) == 3
    assert len(_content_strips(_draw_figures(1))) == 1


def test_portrait_is_cropped_to_a_single_person():
    """这是核心：多视角时裁出**单人**，否则模型会画出三个人。"""
    character_id = 987001
    sheet_rel = media.character_sheet_rel_path(character_id, "png")
    media.save_bytes(sheet_rel, b"")  # 先占位，下面直接写文件
    sheet_abs = media.absolute_path(sheet_rel)
    _draw_figures(3).save(sheet_abs)

    portrait = build_portrait(character_id, sheet_rel)
    assert portrait is not None and portrait.exists()

    with Image.open(sheet_abs) as sheet, Image.open(portrait) as crop:
        assert crop.width < sheet.width * 0.6, "应该只保留一个人物的宽度"
        assert crop.height == sheet.height


def test_single_figure_sheet_is_used_as_is():
    """只有一个人物时不裁切（避免把单人图切坏）。"""
    character_id = 987002
    sheet_rel = media.character_sheet_rel_path(character_id, "png")
    media.save_bytes(sheet_rel, b"")
    sheet_abs = media.absolute_path(sheet_rel)
    _draw_figures(1).save(sheet_abs)

    portrait = build_portrait(character_id, sheet_rel)
    assert portrait is not None
    with Image.open(sheet_abs) as sheet, Image.open(portrait) as crop:
        assert crop.size == sheet.size


def test_portrait_of_missing_sheet_returns_none():
    assert build_portrait(987003, "characters/987003/nope.png") is None
