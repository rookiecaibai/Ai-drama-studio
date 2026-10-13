"""镜头相关接口（目前只有"读取生成好的画面"）。"""

from fastapi import APIRouter
from fastapi.responses import FileResponse

from app.api.deps import CurrentUser, DbSession
from app.core.errors import NotFoundError
from app.db.models import Shot, User
from app.services import media

router = APIRouter(prefix="/shots", tags=["shots"])


def _get_owned_shot(db: DbSession, shot_id: int, user: User) -> Shot:
    """镜头 -> 剧集 -> 短剧 -> 主人，必须一层层校验归属。"""
    shot = db.get(Shot, shot_id)
    if shot is None or shot.episode.drama.owner_id != user.id:
        raise NotFoundError(f"镜头 {shot_id} 不存在")
    return shot


@router.get("/{shot_id}/image", summary="读取镜头画面（需要登录，且只能读自己的）")
def get_shot_image(shot_id: int, db: DbSession, user: CurrentUser) -> FileResponse:
    """为什么不做成 /media/... 静态目录？

    静态目录**没有鉴权**：谁猜到（或拿到）URL 就能看到别人的图。
    用户生成的内容属于私有数据，必须经过"登录 + 归属校验"这一层。
    代价是前端不能用 <img src> 直接引用（浏览器不会自带 Authorization 头），
    需要用 fetch 带 token 取回，再转成 blob URL —— 前端 AuthImage 组件做的就是这件事。
    """
    shot = _get_owned_shot(db, shot_id, user)

    if not shot.image_path or not media.exists(shot.image_path):
        raise NotFoundError("这个镜头还没有生成画面")

    return FileResponse(
        media.absolute_path(shot.image_path),
        media_type="image/png",
        # 图片按镜头 id 固定路径，重新生成会覆盖同名文件，
        # 所以加 no-cache 让浏览器每次都取新的，避免看到旧图
        headers={"Cache-Control": "no-cache"},
    )


@router.get("/{shot_id}/audio", summary="读取镜头配音（需要登录，且只能读自己的）")
def get_shot_audio(shot_id: int, db: DbSession, user: CurrentUser) -> FileResponse:
    """和画面同理：音频也是用户私有内容，必须走鉴权接口。

    注意 Content-Type 必须按实际扩展名给 —— 给错了浏览器播放器会直接拒绝播放。
    """
    shot = _get_owned_shot(db, shot_id, user)

    if not shot.audio_path or not media.exists(shot.audio_path):
        raise NotFoundError("这个镜头还没有配音")

    return FileResponse(
        media.absolute_path(shot.audio_path),
        media_type=media.content_type_for(shot.audio_path),
        headers={"Cache-Control": "no-cache"},
    )
