"""按"短剧"解析模型配置：用户自己填的就用用户的，没填的用服务端默认。

**这是本项目权限模型上最重要的一次变化**，值得说清背景：

改之前：所有人共用服务端 `.env` 里的那几个 Key —— 意思是**用户花的是站长的钱**。
          这在演示项目里可以，但一上线就是灾难：陌生人能刷爆你的账户。
改之后：每个短剧有自己的模型配置，用户填自己的 Key 就用自己的额度；
          没填则回退到服务端默认（方便试玩）。回退行为可以用
          `ALLOW_SERVER_KEY_FALLBACK=false` 关掉，做成严格的 BYOK。

**两条安全底线**（都在这个文件里实现）：

① `base_url` 必须校验 —— 允许用户自定义地址就等于让服务器替他发请求，
   这就是 **SSRF（服务端请求伪造）** 的入口：填 `http://127.0.0.1:6379` 就能探测内网。
   所以我们只放行 `https://` 且**解析后不是内网/环回地址**的 URL。
   （真正的生产环境还应该有出网代理和域名白名单，这里做到"拦住明显的内网探测"。）

② API Key 加密落库、永不回显（见 core/secrets_box.py）。
"""

import ipaddress
import logging
import socket
from dataclasses import dataclass, field
from urllib.parse import urlparse

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.errors import AppError
from app.core.secrets_box import decrypt_key
from app.db.models import Drama, DramaModelSettings
from app.services.images.base import ImageProvider
from app.services.images.factory import build_image_provider
from app.services.llm.base import LLMProvider
from app.services.llm.factory import build_provider as build_llm_provider
from app.services.tts.base import TtsProvider
from app.services.tts.factory import build_tts_provider
from app.services.videos.base import VideoClipProvider
from app.services.videos.factory import build_clip_provider

logger = logging.getLogger(__name__)

#: 支持的配置种类（和前端的分组一一对应）
KINDS = ("llm", "image", "tts", "video")

_KIND_LABEL = {"llm": "文本模型", "image": "文生图模型", "tts": "配音模型", "video": "图生视频模型"}


@dataclass
class ResolvedConfig:
    """一次生成实际用的模型配置（已把"用户填的"和"服务端默认"合并好）。"""

    kind: str
    provider: str
    api_key: str
    base_url: str
    model: str
    extra: dict = field(default_factory=dict)
    #: "drama" = 用户在这个短剧里填的；"server" = 回退到服务端默认
    source: str = "server"


def _is_public_host(host: str) -> bool:
    """判断主机名解析出来的地址是否"可以访问"（即不是内网/环回/链路本地）。"""
    if not host:
        return False
    try:
        # 直接就是 IP 的情况
        address = ipaddress.ip_address(host)
        return not (
            address.is_private or address.is_loopback or address.is_link_local
            or address.is_reserved or address.is_multicast
        )
    except ValueError:
        pass
    # 域名：解析一次再看（DNS rebinding 这类高级攻击这里不处理，够用了）
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        return False
    for info in infos:
        try:
            address = ipaddress.ip_address(info[4][0])
        except ValueError:
            continue
        if address.is_private or address.is_loopback or address.is_link_local:
            return False
    return bool(infos)


def validate_base_url(raw: str) -> str:
    """校验用户填的 base_url，通过则返回规范化后的值。

    **为什么这里必须严格**：这个 URL 会让**服务器**去发请求。
    不校验就等于给了一个"任意内网探测 + 任意出网请求"的入口（SSRF）。
    """
    raw = (raw or "").strip()
    if not raw:
        return ""
    parsed = urlparse(raw)
    if parsed.scheme != "https":
        raise AppError(
            "模型地址必须是 https:// 开头（http 会把你的 API Key 明文发到网络上）",
            code="bad_base_url",
            status_code=400,
        )
    if not parsed.hostname:
        raise AppError("模型地址不合法：缺少域名", code="bad_base_url", status_code=400)
    if not _is_public_host(parsed.hostname):
        raise AppError(
            "模型地址不能指向内网或本机地址（这会带来 SSRF 风险）",
            code="bad_base_url",
            status_code=400,
        )
    return raw.rstrip("/")


def get_settings_row(db: Session, drama_id: int) -> DramaModelSettings | None:
    return db.scalar(select(DramaModelSettings).where(DramaModelSettings.drama_id == drama_id))


def get_or_create_row(db: Session, drama_id: int) -> DramaModelSettings:
    row = get_settings_row(db, drama_id)
    if row is None:
        row = DramaModelSettings(drama_id=drama_id, config={})
        db.add(row)
        db.commit()
        db.refresh(row)
    return row


def resolve(db: Session, drama: Drama, kind: str, settings: Settings) -> ResolvedConfig:
    """把"用户填的"和"服务端默认"合并成一份实际使用的配置。"""
    if kind not in KINDS:
        raise AppError(f"未知的模型种类: {kind}", code="bad_model_kind", status_code=400)

    row = get_settings_row(db, drama.id)
    override: dict = (row.config or {}).get(kind, {}) if row else {}

    user_key = decrypt_key(override.get("api_key_enc", ""))
    # 严格 BYOK 模式：没填自己的 Key 就直接拒绝，**不管有没有改模型名**
    if not user_key and not settings.allow_server_key_fallback:
        raise AppError(
            f"这个短剧的「{_KIND_LABEL[kind]}」还没填 API Key，"
            "而服务端配置为不允许共用默认 Key（ALLOW_SERVER_KEY_FALLBACK=false）",
            code="api_key_required",
            status_code=400,
        )

    # 用户只要填了 Key，就认为这个短剧要用他自己的额度（这是"不公用后端 Key"的核心）
    use_drama = bool(
        user_key
        or override.get("provider")
        or override.get("base_url")
        or override.get("model")
        or override.get("size")
        or override.get("voice")
    )
    if use_drama and not user_key:
        # 用户只改了模型名/地址但没填 Key —— 用服务端 Key 兼容这种情况，
        # 但**必须记一条日志**：这是"用户以为在用自己额度、其实在用你的"的典型误解来源。
        logger.info("短剧 %s 的 %s 只填了模型配置没填 Key，将使用服务端 Key", drama.id, kind)

    if kind == "llm":
        return ResolvedConfig(
            kind=kind,
            provider=override.get("provider") or settings.llm_provider,
            api_key=user_key or settings.llm_api_key,
            base_url=override.get("base_url") or settings.llm_base_url,
            model=override.get("model") or settings.llm_model,
            source="drama" if user_key else "server",
        )
    if kind == "image":
        return ResolvedConfig(
            kind=kind,
            provider=override.get("provider") or settings.image_provider,
            api_key=user_key or settings.image_api_key,
            base_url=override.get("base_url") or settings.image_base_url,
            model=override.get("model") or settings.image_model,
            extra={"size": override.get("size") or settings.image_size},
            source="drama" if user_key else "server",
        )
    if kind == "tts":
        return ResolvedConfig(
            kind=kind,
            provider=override.get("provider") or settings.tts_provider,
            api_key=user_key or settings.tts_api_key,
            base_url=override.get("base_url") or settings.tts_base_url,
            model=override.get("model") or settings.tts_model,
            extra={"voice": override.get("voice") or settings.tts_voice},
            source="drama" if user_key else "server",
        )
    # video
    return ResolvedConfig(
        kind=kind,
        provider=override.get("provider") or settings.video_clip_provider,
        api_key=user_key or settings.video_api_key or settings.image_api_key,
        base_url=override.get("base_url") or settings.video_base_url,
        model=override.get("model") or settings.video_model,
        extra={
            "size": override.get("size") or settings.video_size,
            "send_duration": override.get(
                "send_duration", settings.video_send_duration
            ),
        },
        source="drama" if user_key else "server",
    )


# ── 构造 Provider（业务代码只需要调这四个）──
#
# 关键参数是 `fallback`：**这个短剧没填自己的配置时，直接用传进来的 provider**。
# 为什么不直接"从 settings 造一个"：
#   ① 生产环境里 fallback 就是按服务端 .env 造出来的全局 provider，行为一致；
#   ② 测试里 fallback 是注入的假 provider —— 这样 188 个已有测试的依赖覆盖照样生效，
#      不会因为引入"按短剧配置"就把测试体系推翻。
# 只有用户真的填了配置，我们才自己造一个（那时用假 provider 才是不对的）。


def build_llm(
    db: Session, drama: Drama, settings: Settings, fallback: LLMProvider | None = None
) -> LLMProvider:
    cfg = resolve(db, drama, "llm", settings)
    if cfg.source == "server" and fallback is not None:
        return fallback
    logger.info("短剧 %s 文本模型: %s/%s（Key 来源=%s）", drama.id, cfg.provider, cfg.model, cfg.source)
    # 复用工厂：把合并后的配置塞进一个 Settings 副本，避免维护两套构造逻辑
    merged = settings.model_copy(
        update={
            "llm_provider": cfg.provider,
            "llm_api_key": cfg.api_key,
            "llm_base_url": cfg.base_url,
            "llm_model": cfg.model,
        }
    )
    return build_llm_provider(merged)


def build_image(
    db: Session, drama: Drama, settings: Settings, fallback: ImageProvider | None = None
) -> ImageProvider:
    cfg = resolve(db, drama, "image", settings)
    if cfg.source == "server" and fallback is not None:
        return fallback
    logger.info("短剧 %s 文生图: %s/%s（Key 来源=%s）", drama.id, cfg.provider, cfg.model, cfg.source)
    merged = settings.model_copy(
        update={
            "image_provider": cfg.provider,
            "image_api_key": cfg.api_key,
            "image_base_url": cfg.base_url,
            "image_model": cfg.model,
            "image_size": cfg.extra["size"],
        }
    )
    return build_image_provider(merged)


def build_tts(
    db: Session, drama: Drama, settings: Settings, fallback: TtsProvider | None = None
) -> TtsProvider:
    cfg = resolve(db, drama, "tts", settings)
    if cfg.source == "server" and fallback is not None:
        return fallback
    logger.info("短剧 %s 配音: %s/%s/%s（Key 来源=%s）", drama.id, cfg.provider, cfg.model, cfg.extra["voice"], cfg.source)
    merged = settings.model_copy(
        update={
            "tts_provider": cfg.provider,
            "tts_api_key": cfg.api_key,
            "tts_base_url": cfg.base_url,
            "tts_model": cfg.model,
            "tts_voice": cfg.extra["voice"],
        }
    )
    return build_tts_provider(merged)


def build_video(
    db: Session,
    drama: Drama,
    settings: Settings,
    fallback: VideoClipProvider | None = None,
) -> VideoClipProvider | None:
    cfg = resolve(db, drama, "video", settings)
    if cfg.source == "server" and fallback is not None:
        return fallback
    if cfg.provider == "none":
        return None
    logger.info("短剧 %s 图生视频: %s/%s（Key 来源=%s）", drama.id, cfg.provider, cfg.model, cfg.source)
    merged = settings.model_copy(
        update={
            "video_clip_provider": cfg.provider,
            "video_api_key": cfg.api_key,
            "video_base_url": cfg.base_url,
            "video_model": cfg.model,
            "video_size": cfg.extra["size"],
            "video_send_duration": cfg.extra["send_duration"],
        }
    )
    return build_clip_provider(merged)
