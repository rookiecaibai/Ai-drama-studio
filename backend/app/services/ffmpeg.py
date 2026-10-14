"""ffmpeg 的公共封装（视频合成和配音都会用到）。

抽出来的原因：一开始只有一个地方用 ffmpeg（拼视频），所以代码写在 video_service 里；
现在配音也要变调、拼接，两处都要"找 ffmpeg、跑命令、读时长"。
与其复制一遍，不如提成共享模块 —— **第二处需要它的时候就是抽取的时机**。
"""

import logging
import re
import shutil
import subprocess
from pathlib import Path

from app.core.config import get_settings
from app.core.errors import AppError

logger = logging.getLogger(__name__)

#: 内部统一的工作采样率（单声道）。所有进入 ffmpeg 滤镜的音频先重采样到这个值，
#: 这样变调公式里的采样率就是个已知常量，不用先探测每个文件。
WORK_SAMPLE_RATE = 24000

_ffmpeg_path: str | None = None


def ffmpeg_exe() -> str:
    """找到可用的 ffmpeg。

    优先级：配置指定 -> 系统 PATH -> imageio-ffmpeg 自带的内置二进制。
    最后这层很重要：开发机不用手工装 ffmpeg 也能跑通整条链路。
    """
    global _ffmpeg_path
    if _ffmpeg_path:
        return _ffmpeg_path

    settings = get_settings()

    if settings.ffmpeg_path:
        candidate = shutil.which(settings.ffmpeg_path) or settings.ffmpeg_path
        if Path(candidate).is_file():
            _ffmpeg_path = candidate
            return candidate
        logger.warning("配置的 FFMPEG_PATH 不存在: %s，继续自动查找", settings.ffmpeg_path)

    found = shutil.which("ffmpeg")
    if found:
        _ffmpeg_path = found
        return found

    try:
        import imageio_ffmpeg

        _ffmpeg_path = imageio_ffmpeg.get_ffmpeg_exe()
        logger.info("使用 imageio-ffmpeg 内置的 ffmpeg: %s", _ffmpeg_path)
        return _ffmpeg_path
    except Exception as exc:  # noqa: BLE001
        raise AppError(
            "找不到 ffmpeg：请安装 ffmpeg 并加入 PATH，或用 FFMPEG_PATH 指定路径，"
            "或安装 imageio-ffmpeg（自带二进制）",
            code="ffmpeg_missing",
            status_code=500,
        ) from exc


def run_ffmpeg(args: list[str]) -> None:
    """跑一条 ffmpeg 命令，失败时把最后几行 stderr 带进错误信息（否则排查全靠猜）。"""
    proc = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        tail = " | ".join((proc.stderr or "").strip().splitlines()[-5:])
        logger.error("ffmpeg 失败: %s\n%s", " ".join(args[:6]), proc.stderr)
        raise AppError(f"ffmpeg 执行失败: {tail}", code="ffmpeg_failed", status_code=500)


def probe_duration_sec(path: Path) -> float | None:
    """读出一个媒体文件的时长（秒）。

    用的是 `ffmpeg -i 文件` 打印到 stderr 的 "Duration: 00:00:03.45"。
    （imageio-ffmpeg 只带 ffmpeg 不带 ffprobe，所以用这个办法。）
    """
    proc = subprocess.run(
        [ffmpeg_exe(), "-i", str(path)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    match = re.search(r"Duration:\s*(\d+):(\d+):(\d+\.\d+)", proc.stderr or "")
    if not match:
        return None
    hours, minutes, seconds = match.groups()
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)
