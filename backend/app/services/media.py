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


def shot_image_rel_path(shot_id: int) -> str:
    """某一镜头的画面存哪个相对路径。

    用镜头 id 而不用"剧集+序号"：镜头重新生成时直接覆盖同一个文件，
    天然幂等，也不会留下孤儿文件。
    """
    return f"shots/{shot_id}.png"


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
