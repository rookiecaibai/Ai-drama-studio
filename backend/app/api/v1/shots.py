"""镜头相关接口：读画面/动态片段/配音，以及在分镜表里手动编辑镜头。"""

from fastapi import APIRouter
from fastapi.responses import FileResponse
from sqlalchemy import delete, select

from app.api.deps import CurrentUser, DbSession
from app.core.errors import NotFoundError
from app.db.models import DialogueLine, Shot, User
from app.schemas.shot import ShotRead, ShotUpdate
from app.services import media

router = APIRouter(prefix="/shots", tags=["shots"])


def _get_owned_shot(db: DbSession, shot_id: int, user: User) -> Shot:
    """镜头 -> 剧集 -> 短剧 -> 主人，必须一层层校验归属。"""
    shot = db.get(Shot, shot_id)
    if shot is None or shot.episode.drama.owner_id != user.id:
        raise NotFoundError(f"镜头 {shot_id} 不存在")
    return shot


@router.patch("/{shot_id}", response_model=ShotRead, summary="手动编辑镜头（分镜表里直接改）")
def update_shot(shot_id: int, payload: ShotUpdate, db: DbSession, user: CurrentUser) -> Shot:
    """改一个镜头。

    这里最需要想清楚的是：**改了某个字段之后，之前生成的东西还算不算数？**

    - 改 `dialogue` → 台词是最"派生"的字段：角色分配、情绪、语速、每句音频
      全是从它解析出来的。所以必须**作废全部派生数据**（逐句台词行 + 每句音频 +
      整段镜头音频），否则会出现"台词改了、配音还是老台词"这种自相矛盾的状态。
      作废而不是"尝试修补"：修补要重新跑角色分配和情绪分析（还得调模型花钱），
      而且用户改台词通常意味着语气也变了，重做才是对的。

    - 改 `description` / `image_prompt` / `shot_type` / `camera` / `duration_sec`
      → **不删已生成的画面**。理由是：那张图是"已经花过钱的结果"，
      直接删掉等于替用户扔东西；而它仍然是可用的（只是可能和新描述不完全匹配）。
      要更新的话，用户自己点「生成画面」重跑即可 —— 我们把这个决定权留给他。

    顺带一个细节：作废逐句台词时，**每句的音频文件也要一起删**。
    只删数据库行会在磁盘上留下永远没人引用的孤儿文件（这个坑在删短剧那里踩过）。
    """
    shot = _get_owned_shot(db, shot_id, user)

    dialogue_changed = payload.dialogue is not None and payload.dialogue != shot.dialogue

    if payload.shot_type is not None:
        shot.shot_type = payload.shot_type
    if payload.camera is not None:
        shot.camera = payload.camera
    if payload.description is not None:
        shot.description = payload.description
    if payload.dialogue is not None:
        shot.dialogue = payload.dialogue
    if payload.duration_sec is not None:
        shot.duration_sec = payload.duration_sec
    if payload.image_prompt is not None:
        shot.image_prompt = payload.image_prompt
    if payload.audio_prompt is not None:
        shot.audio_prompt = payload.audio_prompt

    if dialogue_changed:
        # ① 收集要删的音频文件（先查再删，否则删完就找不到路径了）
        line_audios = [
            rel
            for rel in db.scalars(
                select(DialogueLine.audio_path).where(
                    DialogueLine.shot_id == shot.id, DialogueLine.audio_path.is_not(None)
                )
            )
            if rel
        ]
        # ② 删掉逐句台词行（它们是台词解析的产物，下次配音会重新生成）
        db.execute(delete(DialogueLine).where(DialogueLine.shot_id == shot.id))
        # ③ 删掉这些行的音频文件 + 这个镜头的整段音频
        for rel_path in line_audios:
            media.delete(rel_path)
        if shot.audio_path:
            media.delete(shot.audio_path)
        shot.audio_path = None
        shot.audio_error = None

    db.commit()
    db.refresh(shot)
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
        # Content-Type 必须按文件真实格式给 —— 厂商可能返回 JPEG 而不是 PNG。
        # 这里曾经写死 "image/png"，而智谱实际返回 JPEG：浏览器靠嗅探能显示，
        # 但对 CDN、严格校验的客户端就是错的。
        media_type=media.content_type_for(shot.image_path),
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


@router.get("/{shot_id}/clip", summary="预览镜头动态片段（需要登录，且只能读自己的）")
def get_shot_clip(shot_id: int, db: DbSession, user: CurrentUser) -> FileResponse:
    """图生视频产出的动态片段。

    单独提供这个接口的理由：合成成片要等所有镜头就绪，
    而用户想"先看看这一个镜头动起来是什么样"—— 预览比看成品快得多，
    尤其是真实视频模型很慢、很贵的时候，快速预览能省下大量试错成本。
    """
    shot = _get_owned_shot(db, shot_id, user)

    if not shot.clip_path or not media.exists(shot.clip_path):
        raise NotFoundError("这个镜头还没有动态片段")

    return FileResponse(
        media.absolute_path(shot.clip_path),
        media_type=media.content_type_for(shot.clip_path),
        # 同样是按镜头 id 固定路径、重新生成会覆盖，所以别缓存
        headers={"Cache-Control": "no-cache", "Accept-Ranges": "none"},
    )
