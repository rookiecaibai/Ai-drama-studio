"""Ken Burns 运镜：把一张静图变成"会动"的视频（纯本地 ffmpeg，0 成本）。

**这是什么**
纪录片里常用的手法：镜头对着老照片缓慢推近 / 拉远 / 平移，
画面本身没动，但观众的观感是"活"的。
它**不是真正的图生视频**（人物不会走动、不会做动作），
但比完全静止的定格好得多，而且是免费的 —— 用它来：

1. 在没有视频模型 API Key 时，也能把"动态片段 -> 合成"这条链路跑通、测通；
2. 作为真实 i2v 的兜底：某个镜头生成失败或太贵时，退回运镜版本；
3. 成本敏感的场景下，直接当正式方案用。

**为什么用 zoompan 滤镜**
它是 ffmpeg 里专门做"对静态帧做缩放/平移"的滤镜。
关键细节是**先超采样再 zoompan**：直接把 720x1280 的图放大 1.1 倍会看到明显的
像素抖动（因为 x/y 是整数步进），先把源图放大到 2 倍再缩回来，抖动就基本消失了。

**四种运镜按镜头 id 轮换**：同一集里连续几个镜头都用同一种运镜会很呆，
按 id 取模轮换既多样又可复现（测试稳定）。
"""

import logging
from pathlib import Path

from app.services.ffmpeg import ffmpeg_exe, run_ffmpeg

logger = logging.getLogger(__name__)

#: 超采样倍数。2 倍是"抖动基本消失"和"文件别太大"之间的平衡点。
SUPERSAMPLE = 2

#: 运镜幅度：最多放大 12%。再大就会明显看着像"缩放动画"而不是运镜。
ZOOM_TO = 1.12

#: 平移时固定的缩放（平移必须放大一点才有可移动的余量）
PAN_ZOOM = 1.15


def _zoompan_expr(variant: int, *, frames: int, width: int, height: int, fps: int) -> str:
    """构造 zoompan 表达式。

    刻意用 `on`（输出帧序号）做纯函数式表达，而不是依赖 `zoom` 的累积状态 ——
    累积写法在首帧初始值上很容易出错（比如"拉远"一开始就被夹到 1.0 导致完全不动）。
    """
    step = (ZOOM_TO - 1.0) / max(1, frames)
    center_x = "iw/2-(iw/zoom/2)"
    center_y = "ih/2-(ih/zoom/2)"

    if variant == 0:  # 缓慢推近（最稳，默认）
        zoom = f"'min(1+{step:.6f}*on,{ZOOM_TO})'"
        x, y = f"'{center_x}'", f"'{center_y}'"
    elif variant == 1:  # 缓慢拉远
        zoom = f"'max({ZOOM_TO}-{step:.6f}*on,1.0)'"
        x, y = f"'{center_x}'", f"'{center_y}'"
    elif variant == 2:  # 向右平移
        zoom = f"'{PAN_ZOOM}'"
        x, y = f"'(iw-iw/zoom)*on/{frames}'", f"'{center_y}'"
    else:  # 向左平移
        zoom = f"'{PAN_ZOOM}'"
        x, y = f"'(iw-iw/zoom)*(1-on/{frames})'", f"'{center_y}'"

    return (
        f"zoompan=z={zoom}:x={x}:y={y}:d={frames}:"
        f"s={width}x{height}:fps={fps}"
    )


def render_ken_burns(
    image_path: Path,
    out_path: Path,
    *,
    seconds: float,
    width: int,
    height: int,
    fps: int,
    variant: int = 0,
) -> None:
    """生成一段运镜视频。"""
    frames = max(1, int(seconds * fps))
    supersampled_w = width * SUPERSAMPLE
    supersampled_h = height * SUPERSAMPLE

    # 先"cover"式铺满超采样画布：等比放大到盖住画布，再居中裁掉多余部分。
    # 这样不同长宽比的输入图都不会变形，也不会有黑边。
    pre = (
        f"scale={supersampled_w}:{supersampled_h}:force_original_aspect_ratio=increase,"
        f"crop={supersampled_w}:{supersampled_h}"
    )
    filters = f"{pre},{_zoompan_expr(variant % 4, frames=frames, width=width, height=height, fps=fps)},setsar=1"

    run_ffmpeg(
        [
            ffmpeg_exe(),
            "-y",
            "-loop",
            "1",
            "-i",
            str(image_path),
            "-vf",
            filters,
            # -t 一定要有：-loop 1 的输入是无限的，zoompan 会给每个输入帧产出 frames 帧，
            # 不掐断就会一直编下去。
            "-t",
            f"{seconds:.2f}",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "26",
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
            str(out_path),
        ]
    )
    logger.info("Ken Burns 片段生成完成：%s（运镜模式 %s）", out_path.name, variant % 4)
