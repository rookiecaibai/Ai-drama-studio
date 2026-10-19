"""从三视图里裁出"单人单视角"，供参考图使用。

**为什么必须这一步**（实测事故）：
把整张三视图（三个视角并排）直接当参考图喂给模型，模型会以为**那是三个人**，
于是画出一排三个一模一样的人 —— 完全不是我们要的"一个角色站在场景里"。

实测对比（同一个角色、同一句提示词）：
    整张三视图当参考图 -> 三个一模一样的人并排站着 ❌
    裁出中间那个视角当参考图 -> 只有一个人，长相/发型/服装都保持一致 ✅

**为什么用"检测内容列"而不是固定切三等份**：
三视图的排版不稳定（有时候模型画的是"侧/正/侧"，有时候只画了一个人）。
固定切三份会把单人图切坏。所以这里先找"有内容的连续列块"：
- 找到 ≥2 个块 -> 是多视角排版，取最宽的那个（通常是最完整的那个人）
- 只有 1 个块 -> 本来就是单人图，原样返回
"""

import logging
from pathlib import Path

from PIL import Image

from app.services import media

logger = logging.getLogger(__name__)

#: 扫描用的缩小尺寸：原图 1M 像素逐列扫太慢，缩到 192 宽再扫（快 20 倍，精度够）
_SCAN_WIDTH = 192
#: 窄于这个比例的"内容块"视为噪点（比如右下角的签名）
_MIN_RUN_RATIO = 0.06
#: 这一列至少要有这么大比例的像素是"墨"，才算有内容
_MIN_INK_RATIO = 0.08


def _content_strips(image: Image.Image) -> list[tuple[int, int]]:
    """找出"竖直方向有内容的连续列块"，返回原图坐标的 [(x0, x1), ...]。

    **不要用固定的"白色阈值"判断背景**（第一版就是这么写的，被万相打脸了）：
    智谱/万相生成的设定图背景常常是**浅灰渐变**，固定阈值会把整幅图都判成有内容，
    于是三块检测成一块，裁剪整个失效 —— 参考图又变回"三个人"。
    所以这里：
      ① 用**全图中位数**估背景亮度（背景占绝大多数像素），阈值取"比背景暗 25"；
      ② 只统计**中间竖带**（15%~85%），避开脚下的地面阴影 —— 阴影会把人物之间连起来。
    """
    gray = image.convert("L")
    width, height = gray.size
    scale = width / _SCAN_WIDTH
    small = gray.resize((_SCAN_WIDTH, max(1, int(height / scale))), Image.BILINEAR)
    data = small.tobytes()
    scan_w, scan_h = small.size

    # 采样取中位数（全排序 1M 个字节没必要）
    sample = sorted(data[::7])
    background = sample[len(sample) // 2] if sample else 255
    threshold = min(background - 25, 235)

    y0 = int(scan_h * 0.15)
    y1 = int(scan_h * 0.85)
    rows = max(1, y1 - y0)
    ink = [0] * scan_w
    for y in range(y0, y1):
        row = data[y * scan_w : (y + 1) * scan_w]
        for x, value in enumerate(row):
            if value < threshold:
                ink[x] += 1

    has_content = [(count / rows) > _MIN_INK_RATIO for count in ink]

    runs: list[tuple[int, int]] = []
    start: int | None = None
    for x, flag in enumerate(has_content):
        if flag and start is None:
            start = x
        elif not flag and start is not None:
            runs.append((start, x))
            start = None
    if start is not None:
        runs.append((start, scan_w))

    # 过滤太窄的噪点，再映射回原图坐标
    result = []
    for x0, x1 in runs:
        if (x1 - x0) / scan_w < _MIN_RUN_RATIO:
            continue
        result.append((int(x0 * scale), min(width, int(x1 * scale))))
    return result


def build_portrait(character_id: int, sheet_rel_path: str) -> Path | None:
    """从三视图生成"单人人像"（裁出最完整的那个视角），返回文件路径。

    结果缓存成 `characters/{id}/portrait.png`：同一张三视图只裁一次；
    三视图重新生成后（文件名里的扩展名可能变）会按需重建。
    """
    if not sheet_rel_path or not media.exists(sheet_rel_path):
        return None

    portrait_rel = media.character_portrait_rel_path(character_id)
    portrait_abs = media.absolute_path(portrait_rel)
    sheet_abs = media.absolute_path(sheet_rel_path)

    if portrait_abs.exists() and portrait_abs.stat().st_mtime >= sheet_abs.stat().st_mtime:
        return portrait_abs  # 已经裁过，且三视图没更新

    try:
        with Image.open(sheet_abs) as sheet:
            image = sheet.convert("RGB")
            strips = _content_strips(image)
            if len(strips) >= 2:
                # 取**最左边**那个人：我们的三视图提示词要求的顺序是
                # 「正面 → 正侧面 → 背面」，所以最左边就是正面。
                # 参考图用正脸效果最好（侧面照给模型的信息少一半）。
                x0, x1 = strips[0]
                margin = max(4, int((x1 - x0) * 0.08))
                box = (max(0, x0 - margin), 0, min(image.width, x1 + margin), image.height)
                image = image.crop(box)
                logger.info(
                    "角色 %s 三视图检测到 %s 个人物，取最左边的正面裁成参考图 (%s)",
                    character_id,
                    len(strips),
                    f"{image.width}x{image.height}",
                )
            else:
                logger.info("角色 %s 的三视图看起来只有一个人物，直接当参考图用", character_id)
            portrait_abs.parent.mkdir(parents=True, exist_ok=True)
            image.save(portrait_abs, format="PNG")
    except Exception as exc:  # noqa: BLE001 —— 裁剪失败不该让整张图生成失败
        logger.warning("角色 %s 裁参考图失败，退回用整张三视图: %s", character_id, exc)
        return sheet_abs

    return portrait_abs
