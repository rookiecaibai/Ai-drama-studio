"""音频后处理：变调、静音、拼接。

**为什么需要变调**
智谱 GLM-TTS 只有 7 个音色，其中能用的男声只有 1 个（xiaochen 134Hz，实测）。
短剧里男角色通常不止一个，所以靠"同一音色 + 音高偏移"派生出
年轻/中年/年老的差别 —— 这就是 shift_pitch 的用途。

**变调但不变速的原理**（这是音频处理里的经典技巧）：
    asetrate  改变采样率 -> 声音变调，同时时长也变了
    aresample 把采样率还原 -> 保持波形数据不变
    atempo    反向补偿时长 -> 时长回到原样
净效果：音高变了，时长没变。

**为什么进来先 aresample 到固定采样率**
否则公式里的 asetrate 要依赖输入文件的实际采样率（每家 TTS 厂商都不一样），
先统一重采样，后面就是确定的算法。
"""

import logging
import math
import struct
import wave
from pathlib import Path

from app.services.ffmpeg import WORK_SAMPLE_RATE, ffmpeg_exe, run_ffmpeg

logger = logging.getLogger(__name__)

#: 变调幅度上限。太夸张会明显失真（听起来像机器人或快放），所以夹住。
MAX_PITCH_SHIFT = 0.25


def shift_pitch(src: Path, dst: Path, pitch_shift: float) -> None:
    """把 src 变调后写到 dst，**时长保持不变**。

    pitch_shift: 0.06 = 升高 6%（更年轻/更尖），-0.09 = 降低 9%（更年长/更沉）。
    """
    pitch_shift = max(-MAX_PITCH_SHIFT, min(MAX_PITCH_SHIFT, pitch_shift))
    factor = 1.0 + pitch_shift

    if abs(pitch_shift) < 0.005:
        # 不需要变调就直接复制，省一次编码（也避免无谓的音质损失）
        dst.write_bytes(src.read_bytes())
        return

    rate = WORK_SAMPLE_RATE
    filter_chain = (
        f"aresample={rate},"  # 先统一采样率，让下面的数值确定
        f"asetrate={int(rate * factor)},"  # 改采样率 = 变调
        f"aresample={rate},"  # 采样率还原
        f"atempo={1.0 / factor:.6f}"  # 时长补偿回来
    )
    run_ffmpeg(
        [
            ffmpeg_exe(),
            "-y",
            "-i",
            str(src),
            "-filter:a",
            filter_chain,
            "-ar",
            str(rate),
            "-ac",
            "1",
            str(dst),
        ]
    )


def write_silence(dst: Path, seconds: float) -> None:
    """生成一段静音 WAV（句与句之间的停顿）。

    用标准库 wave 直接写，不用调 ffmpeg —— 纯静音没必要走外部进程。
    """
    frames = max(1, int(WORK_SAMPLE_RATE * max(0.0, seconds)))
    buffer = bytearray()
    for _ in range(frames):
        buffer += struct.pack("<h", 0)
    with wave.open(str(dst), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(WORK_SAMPLE_RATE)
        handle.writeframes(bytes(buffer))


def concat_audio(parts: list[Path], dst: Path) -> None:
    """把多段音频按顺序拼成一段。

    用 concat 滤镜而不是 concat 分离器：滤镜会**重采样对齐**，
    所以各段采样率/声道数不同也能拼（不同厂商产出的格式确实可能不一样）。
    """
    if not parts:
        raise ValueError("没有可拼接的音频")

    args = [ffmpeg_exe(), "-y"]
    for part in parts:
        args += ["-i", str(part)]

    labels = "".join(f"[{i}:a]" for i in range(len(parts)))
    args += [
        "-filter_complex",
        f"{labels}concat=n={len(parts)}:v=0:a=1[aout]",
        "-map",
        "[aout]",
        "-ar",
        str(WORK_SAMPLE_RATE),
        "-ac",
        "1",
        str(dst),
    ]
    run_ffmpeg(args)


def gap_seconds(lengths: list[int], base: float = 0.22) -> float:
    """句间停顿多长。

    按文本长度微调：短促的对话（"在。""谁？"）留短一点更自然，
    长句之后停顿长一点，听起来像真的换气。
    """
    if not lengths:
        return base
    average = sum(lengths) / len(lengths)
    # 平均 8 字以内 -> 0.18s；越长越接近 0.3s
    ratio = max(0.0, min(1.0, (average - 4) / 16))
    return round(0.18 + 0.12 * ratio, 3)


def duration_seconds(path: Path) -> float:
    """音频时长；读不出来时返回 0（上层不应因为读不到时长就失败）。"""
    from app.services.ffmpeg import probe_duration_sec

    value = probe_duration_sec(path)
    if value is None or not math.isfinite(value):
        return 0.0
    return value
