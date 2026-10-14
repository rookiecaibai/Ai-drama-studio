"""用 ffmpeg 把一集的镜头合成成竖屏 MP4。

合成思路（这是最稳、也最容易理解的做法）：
    每个镜头 -> 一段小视频（图片定格 + 该镜头的配音）-> 所有小视频首尾相接 -> 成片

为什么不用"一条 ffmpeg 命令搞定"：
分镜数量不固定，用一条命令要动态拼极长的 filter_complex，出错时几乎无法调试。
拆成"每个镜头一段、最后 concat 拼接"，每步都简单、日志清晰、失败能定位到具体镜头。

几个关键细节（都是踩过才知道的）：
1. **没有配音的镜头要补一段静音**：否则拼接时有的片段有音轨、有的没有，
   用 concat 直接复制流（-c copy）就会错位甚至失败。
2. **画面要缩放+加黑边（pad）到统一尺寸**：镜头图可能是任意长宽比，
   尺寸不一致的片段无法用 -c copy 拼接。
3. **-movflags +faststart**：把 moov 元数据放到文件开头，
   浏览器才能"边下边播"，而不是等整个文件下完。
4. **有配音的镜头用 -shortest**：片段长度跟着语音走，避免把台词掐断；
   没有配音的镜头则用镜头自己的 duration_sec。
"""

import logging
import re
import shutil
import subprocess
import uuid
from collections.abc import Callable
from pathlib import Path

from app.core.config import get_settings
from app.core.errors import AppError
from app.db.models import Episode, Shot
from app.services import media
# ffmpeg 的公共封装已提到 app/services/ffmpeg.py（配音也要用它变调和拼接）。
# 这里 re-export 是为了保持原有导入路径可用（测试和别处一直从本模块 import）。
from app.services.ffmpeg import ffmpeg_exe, probe_duration_sec, run_ffmpeg  # noqa: F401

logger = logging.getLogger(__name__)

AUDIO_RATE = 44100
SPEAKER = "成片"


def _build_clip_args(*, exe: str, shot: Shot, image: Path, audio: Path | None, out: Path) -> list[str]:
    settings = get_settings()
    width, height, fps = settings.video_width, settings.video_height, settings.video_fps

    args = [exe, "-y", "-loop", "1", "-i", str(image)]
    if audio is not None:
        args += ["-i", str(audio)]
    else:
        # 没有配音就补静音，保证每段都有音轨（否则拼接会错位）
        args += ["-f", "lavfi", "-i", f"anullsrc=r={AUDIO_RATE}:cl=stereo"]

    # 等比缩放后居中加黑边，保证所有片段尺寸完全一致
    filter_v = (
        f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={fps}"
    )
    args += [
        "-vf",
        filter_v,
        "-c:v",
        "libx264",
        "-preset",
        settings.video_preset,
        "-crf",
        str(settings.video_crf),
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        "-ar",
        str(AUDIO_RATE),
        "-ac",
        "2",
    ]

    if audio is not None:
        args += ["-shortest"]  # 长度跟着语音走，别把台词掐断
    else:
        args += ["-t", f"{max(0.5, shot.duration_sec):.2f}"]

    args += ["-movflags", "+faststart", str(out)]
    return args


def compose_episode_video(
    *,
    episode: Episode,
    shots: list[Shot],
    on_total: Callable[[int], None] | None = None,
    on_progress: Callable[[], None] | None = None,
) -> str:
    """合成成片，返回视频的相对路径。"""
    if not shots:
        raise AppError("这一集还没有分镜，请先生成分镜", code="no_shots", status_code=400)

    missing = [s.number for s in shots if not s.image_path or not media.exists(s.image_path)]
    if missing:
        preview = "、".join(str(n) for n in missing[:8])
        raise AppError(
            f"还有 {len(missing)} 个镜头没有画面（第 {preview} 个），请先生成画面",
            code="images_missing",
            status_code=400,
        )

    exe = ffmpeg_exe()
    settings = get_settings()
    work_dir = media.build_work_dir(f"video-{episode.id}-{uuid.uuid4().hex[:8]}")

    if on_total is not None:
        on_total(len(shots))

    try:
        clips: list[Path] = []
        for index, shot in enumerate(shots, start=1):
            clip = work_dir / f"clip_{index:03d}.mp4"
            audio = (
                media.absolute_path(shot.audio_path)
                if shot.audio_path and media.exists(shot.audio_path)
                else None
            )
            run_ffmpeg(
                _build_clip_args(
                    exe=exe,
                    shot=shot,
                    image=media.absolute_path(shot.image_path or ""),
                    audio=audio,
                    out=clip,
                )
            )
            clips.append(clip)
            if on_progress is not None:
                on_progress()

        # 拼接：所有片段编码参数完全一致，所以可以直接 -c copy（快，且不重新编码、不掉画质）
        list_file = work_dir / "clips.txt"
        list_file.write_text(
            "".join(f"file '{clip.as_posix()}'\n" for clip in clips),
            encoding="utf-8",
        )

        out_rel = media.episode_video_rel_path(episode.id)
        out_abs = media.absolute_path(out_rel)
        out_abs.parent.mkdir(parents=True, exist_ok=True)

        run_ffmpeg(
            [
                exe,
                "-y",
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                str(list_file),
                "-c",
                "copy",
                "-movflags",
                "+faststart",
                str(out_abs),
            ]
        )
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)

    duration = probe_duration_sec(out_abs)
    logger.info(
        "剧集 %s 成片完成：%s 个镜头，文件 %s，时长 %s 秒（%sx%s@%sfps）",
        episode.id,
        len(clips),
        out_abs.name,
        f"{duration:.1f}" if duration else "未知",
        settings.video_width,
        settings.video_height,
        settings.video_fps,
    )
    return out_rel
