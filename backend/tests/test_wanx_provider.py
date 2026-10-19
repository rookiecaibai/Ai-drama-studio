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
    """核心：多视角时裁出**单人**，否则模型会画出三个人。

    断言的是**语义**（人像里只剩一个人物），不是具体像素尺寸 ——
    因为尺寸不安全时还会被归一化到 640x1024，用尺寸断言会失效（这个测试就因此红过一次）。
    """
    character_id = 987001
    sheet_rel = media.character_sheet_rel_path(character_id, "png")
    media.save_bytes(sheet_rel, b"")
    sheet_abs = media.absolute_path(sheet_rel)
    _draw_figures(3).save(sheet_abs)

    portrait = build_portrait(character_id, sheet_rel)
    assert portrait is not None and portrait.exists()

    with Image.open(portrait) as crop:
        strips = _content_strips(crop.convert("RGB"))
    assert len(strips) == 1, f"人像里应该只有一个人物，实际检测到 {len(strips)} 个"


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


def test_grey_gradient_background_still_detected():
    """回归测试：万相生成的背景是**浅灰渐变**，不是纯白。

    第一版裁剪用的是固定的"白色阈值 235"，结果整幅图都被判成有内容 ->
    三块检测成一块 -> 裁剪失效 -> 参考图又变回整张三视图 -> 模型画出三个人。
    这个坑是真踩过的，所以用"浅灰底 + 渐变"的图把它锁住。
    """
    width, height = 1344, 768
    image = Image.new("RGB", (width, height), (238, 240, 242))
    draw = ImageDraw.Draw(image)
    # 纵向渐变，模拟摄影棚背景的光照不均
    for y in range(0, height, 8):
        shade = min(252, 232 + y // 40)
        draw.rectangle([0, y, width, y + 7], fill=(shade, shade + 2, shade + 4))
    slot = width // 3
    for index in range(3):
        left = index * slot + slot // 4
        right = (index + 1) * slot - slot // 4
        draw.rectangle([left, 150, right, 620], fill=(40, 60, 90))

    assert len(_content_strips(image)) == 3


def test_narrow_portrait_is_normalized_to_a_safe_canvas():
    """回归：**竖屏**三视图裁出来的细条会被万相拒收。

    真实事故：智谱画的 768x1344 竖屏三视图，裁出单个角色只有 138x1344（长宽比 1:9.7），
    万相返回 `Error validating image ... resolution must ...`，两张画面直接失败。
    所以尺寸不安全的参考图要归一化到安全画布。
    """
    from app.services.portrait import _PORTRAIT_SIZE, _needs_normalize, _normalize

    narrow = Image.new("RGB", (138, 1344), (40, 60, 90))
    assert _needs_normalize(narrow) is True
    assert _normalize(narrow).size == _PORTRAIT_SIZE

    # 267x768 是万相能接受的（实测 200），不该被加白边改变内容
    acceptable = Image.new("RGB", (267, 768), (40, 60, 90))
    assert _needs_normalize(acceptable) is False


def test_portrait_from_a_portrait_sheet_is_normalized():
    """竖屏三视图（三个视角挤在 768 宽里）裁出来必须归一化，否则会被拒。"""
    from app.services.portrait import _PORTRAIT_SIZE

    character_id = 987004
    sheet_rel = media.character_sheet_rel_path(character_id, "png")
    media.save_bytes(sheet_rel, b"")
    _draw_figures(3, width=768, height=1344).save(media.absolute_path(sheet_rel))

    portrait = build_portrait(character_id, sheet_rel)
    assert portrait is not None
    with Image.open(portrait) as crop:
        assert crop.size == _PORTRAIT_SIZE, "细条必须被归一化，否则万相会 400"


def test_portrait_of_missing_sheet_returns_none():
    assert build_portrait(987003, "characters/987003/nope.png") is None
