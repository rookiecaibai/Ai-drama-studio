"""短剧设置：改名 + 每个模态的模型配置（用户带自己的 API Key）。

**为什么单独一个文件**：dramas.py 已经承担了"剧集/角色/大纲"等职责，
再把密钥加密、SSRF 校验、掩码逻辑塞进去会很快失控。这里只做两件事：
读设置、写设置。
"""

from fastapi import APIRouter

from app.api.deps import CurrentUser, DbSession
from app.api.v1.dramas import _get_owned_drama
from app.core.config import get_settings
from app.core.errors import AppError
from app.core.secrets_box import encrypt_key, mask_key
from app.schemas.drama_settings import (
    DramaSettingsRead,
    DramaSettingsUpdate,
    ModalityRead,
)
from app.services import model_config

router = APIRouter(prefix="/dramas", tags=["drama-settings"])


def _read_modality(db, drama, kind: str, settings) -> ModalityRead:
    """某个模态"当前生效的配置"。

    注意这里的一个安全细节：**只在 source=="drama" 时才返回 has_key / 掩码**。
    如果用户没填、用的是服务端 Key，就不能把服务端 Key 的前 6 位露出去 ——
    掩码看似人畜无害，但它会让别人知道"这把 Key 长什么样"。
    """
    cfg = model_config.resolve(db, drama, kind, settings)
    from_user = cfg.source == "drama"
    return ModalityRead(
        provider=cfg.provider,
        base_url=cfg.base_url,
        model=cfg.model,
        size=cfg.extra.get("size") or "",
        voice=cfg.extra.get("voice") or "",
        send_duration=cfg.extra.get("send_duration"),
        has_key=bool(cfg.api_key) and from_user,
        api_key_hint=mask_key(cfg.api_key) if from_user else "",
        source=cfg.source,
    )


def _read_settings(db, drama, settings) -> DramaSettingsRead:
    return DramaSettingsRead(
        id=drama.id,
        title=drama.title,
        genre=drama.genre,
        logline=drama.logline,
        style=drama.style,
        llm=_read_modality(db, drama, "llm", settings),
        image=_read_modality(db, drama, "image", settings),
        tts=_read_modality(db, drama, "tts", settings),
        video=_read_modality(db, drama, "video", settings),
        allow_server_key_fallback=settings.allow_server_key_fallback,
    )


@router.get("/{drama_id}/settings", response_model=DramaSettingsRead, summary="读取短剧设置")
def get_drama_settings(drama_id: int, db: DbSession, user: CurrentUser) -> DramaSettingsRead:
    drama = _get_owned_drama(db, drama_id, user)
    return _read_settings(db, drama, get_settings())


@router.patch("/{drama_id}/settings", response_model=DramaSettingsRead, summary="修改短剧设置（含模型与 API Key）")
def update_drama_settings(
    drama_id: int,
    payload: DramaSettingsUpdate,
    db: DbSession,
    user: CurrentUser,
) -> DramaSettingsRead:
    """更新短剧设置。

    三个语义细节值得说明：

    ① **只更新传进来的字段**。前端一次只改一块（比如只改文生图），
       不该要求把四个模态全回传 —— 那样一次漏传就会把别的配置清空。
    ② **`api_key` 传空字符串 = 清除已保存的 Key**（回到用服务端默认），
       传 `null`/不传 = 保持原样。用"空串"而不是"null"表达"清除"，
       是因为 JSON 里两者语义不同，而"清除"必须是一个显式动作。
    ③ **`base_url` 会做 SSRF 校验**（只放行 https 且非内网地址），
       见 `model_config.validate_base_url`。
    """
    drama = _get_owned_drama(db, drama_id, user)
    settings = get_settings()

    # ── ① 短剧信息 ──
    if payload.title is not None:
        title = payload.title.strip()
        if not title:
            raise AppError("短剧名称不能为空", code="bad_title", status_code=400)
        drama.title = title[:120]
    if payload.genre is not None:
        drama.genre = payload.genre.strip()[:40]
    if payload.logline is not None:
        drama.logline = payload.logline.strip()[:500]
    if payload.style is not None:
        drama.style = payload.style.strip()[:200]

    # ── ② 模型配置 ──
    row = model_config.get_or_create_row(db, drama.id)
    config = dict(row.config or {})
    for kind in model_config.KINDS:
        override = getattr(payload, kind, None)
        if override is None:
            continue
        current = dict(config.get(kind, {}))

        if override.provider is not None:
            current["provider"] = override.provider.strip()
        if override.base_url is not None:
            current["base_url"] = model_config.validate_base_url(override.base_url)
        if override.model is not None:
            current["model"] = override.model.strip()
        if override.size is not None:
            current["size"] = override.size.strip()
        if override.voice is not None:
            current["voice"] = override.voice.strip()
        if override.send_duration is not None:
            current["send_duration"] = override.send_duration
        if override.api_key is not None:
            # 空串 = 清除；非空 = 加密后保存（**明文不落库**）
            current["api_key_enc"] = encrypt_key(override.api_key)

        config[kind] = current

    # 整份替换而不是原地改：SQLAlchemy 的 JSON 列不会追踪 dict 内部变更，
    # 必须重新赋值才会被标记为"脏"（这个坑很隐蔽）
    row.config = config
    db.commit()
    db.refresh(drama)

    return _read_settings(db, drama, settings)
