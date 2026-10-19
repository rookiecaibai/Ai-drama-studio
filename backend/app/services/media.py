"""媒体文件（生成的图片）的落盘与路径管理。

为什么不用"存外部 URL"：
厂商给的图片地址通常是临时链接（几小时就失效），存下来过两天就打不开了。
所以我们的原则是：**图片下载到自己的磁盘，数据库里只存相对路径**。
（生产环境把 media_root 换成对象存储 OSS/S3 即可，这一层就是抽象点。）

为什么不直接挂一个静态目录给浏览器访问：
静态目录**没有鉴权** —— 谁猜到 URL 谁就能看到别人的图。
用户生成的内容必须走"带登录校验的接口"（见 api/v1/shots.py）。
"""

import logging
from pathlib import Path

from app.core.config import get_settings

logger = logging.getLogger(__name__)


def media_root() -> Path:
    """媒体根目录（相对 backend 目录）。不存在就创建。"""
    root = Path(get_settings().media_root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def shot_image_rel_path(shot_id: int, extension: str = "png") -> str:
    """某一镜头的画面存哪个相对路径。

    用镜头 id 而不用"剧集+序号"：镜头重新生成时直接覆盖同一个文件，
    天然幂等，也不会留下孤儿文件。

    扩展名由调用方给出（不能写死！）—— 见 detect_image_extension 的说明。
    """
    return f"shots/{shot_id}.{extension.lstrip('.')}"


#: 图片格式的魔数（文件头字节 -> 扩展名）
_IMAGE_MAGIC = (
    (b"\x89PNG\r\n\x1a\n", "png"),
    (b"\xff\xd8\xff", "jpg"),
    (b"GIF87a", "gif"),
    (b"GIF89a", "gif"),
)


def detect_image_extension(data: bytes) -> str:
    """从文件头判断图片真实格式。

    **为什么必须有这一步（真实踩到的坑）**：
    我们最初把厂商返回的图片一律按 `.png` 存盘、并按 `image/png` 返回给浏览器。
    但智谱返回的其实是 **JPEG** —— 结果就是"文件内容是 JPEG，扩展名和
    Content-Type 都说是 PNG"。
    浏览器会嗅探格式所以看着正常，可这是明确的技术错误：
    CDN、严格校验的客户端、需要按 Content-Type 处理的下游都会出问题。

    **教训：厂商返回的二进制格式不要靠假设，要靠文件头判断。**
    """
    for magic, extension in _IMAGE_MAGIC:
        if data.startswith(magic):
            return extension
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return "png"  # 认不出来时按 png 存（至少是个自洽的默认值）


def shot_audio_rel_path(shot_id: int, extension: str = "mp3") -> str:
    """某一镜头的配音存哪里。

    音频扩展名取决于厂商（mock 出 wav、真实厂商常出 mp3），所以由调用方指定。
    """
    return f"audio/shots/{shot_id}.{extension.lstrip('.')}"


def episode_video_rel_path(episode_id: int) -> str:
    """某一集的成片存哪里（一集一个文件，重新合成直接覆盖）。"""
    return f"videos/episodes/{episode_id}.mp4"


def shot_clip_rel_path(shot_id: int) -> str:
    """某一个镜头的动态片段存哪里（图生视频的产物）。

    用镜头 id 命名：重新生成直接覆盖，天然幂等，也不留孤儿文件。
    """
    return f"clips/shots/{shot_id}.mp4"


def character_sheet_rel_path(character_id: int, extension: str = "png") -> str:
    """角色三视图存哪里（一个角色一张设定图，重新生成直接覆盖）。

    扩展名由调用方按**文件头**决定，而不是假设厂商返回 PNG —— 智谱实际返回 JPEG。
    """
    return f"characters/{character_id}/sheet.{extension.lstrip('.')}"


def character_portrait_rel_path(character_id: int) -> str:
    """从三视图裁出来的"单人人像"，专门给**参考图**用。

    为什么不直接用三视图：三个视角并排会被模型理解成"三个人"，
    于是画出一排一模一样的角色（实测过）。裁剪见 services/portrait.py。
    这个文件是中间产物，不需要入库、不需要暴露给前端。
    """
    return f"characters/{character_id}/portrait.png"


def line_audio_rel_path(line_id: int) -> str:
    """单句台词的音频存哪里。

    为什么要逐句存：一个镜头里可能有多个人说话，每句要用各自角色的音色合成；
    逐句落盘后，某一句失败只要重做那一句，不用整段重来。
    """
    return f"audio/lines/{line_id}.wav"


def build_work_dir(name: str) -> Path:
    """给需要中间文件的活儿（比如视频合成的分镜片段）准备一个临时目录。

    刻意放在 media 目录下而不是系统临时目录：
    1. media 是我们确定可写、且在 .gitignore 里的位置；
    2. 某些受限环境下 Python 的 tempfile 拿不到可写的系统临时目录；
    3. 放在一起便于排查（虽然正常情况下用完就删）。
    """
    work_dir = media_root() / "_work" / name
    work_dir.mkdir(parents=True, exist_ok=True)
    return work_dir


_MEDIA_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".wav": "audio/wav",
    ".mp3": "audio/mpeg",
    ".m4a": "audio/mp4",
    ".mp4": "video/mp4",
}


def content_type_for(rel_path: str) -> str:
    """按扩展名给出正确的 Content-Type。

    别小看这一步：Content-Type 给错，浏览器播放器会直接拒绝播放
    （音频/视频尤其敏感）。
    """
    suffix = Path(rel_path).suffix.lower()
    return _MEDIA_TYPES.get(suffix, "application/octet-stream")


def absolute_path(rel_path: str) -> Path:
    """把数据库里的相对路径还原成磁盘绝对路径。

    注意：这里做了"不允许跳出 media_root"的保护 ——
    路径里的 `..` 之类的构造不能读到目录外的文件（防目录穿越）。
    """
    root = media_root()
    target = (root / rel_path).resolve()
    if root not in target.parents and target != root:
        raise ValueError(f"非法的媒体路径: {rel_path}")
    return target


def save_bytes(rel_path: str, data: bytes) -> None:
    path = absolute_path(rel_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def exists(rel_path: str | None) -> bool:
    if not rel_path:
        return False
    try:
        return absolute_path(rel_path).is_file()
    except ValueError:
        return False


def delete(rel_path: str | None) -> None:
    """删除一个媒体文件。

    为什么需要它：**数据库的级联删除不会删磁盘上的文件**。
    删掉一部短剧，它所有镜头的图片记录会跟着消失，但 png 文件还留在磁盘上 ——
    时间一长就是一堆没人认领的孤儿文件。所以删除业务数据时要顺手清理文件。

    删文件失败（比如被占用）不应该让整个接口报错，记录一条日志即可。
    """
    if not rel_path:
        return
    try:
        absolute_path(rel_path).unlink(missing_ok=True)
    except (ValueError, OSError) as exc:
        logger.warning("删除媒体文件失败 %s: %s", rel_path, exc)
