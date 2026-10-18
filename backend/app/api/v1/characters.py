"""角色表接口：查音色、改角色卡、读三视图。

角色既是"配音的选角表"，也是"画画面的外貌锚点"—— 两边共用同一份角色数据，
但关心的字段不同：
- 配音关心：gender / age_group / voice / pitch_shift / speed
- 画面关心：gender / age / appearance / occupation / look_prompt
所以一张卡上两类信息都有，用户改一处，配音和画面都会跟着变。
"""

from fastapi import APIRouter
from fastapi.responses import FileResponse

from app.api.deps import CurrentUser, DbSession
from app.core.errors import NotFoundError
from app.db.models import Character, User
from app.schemas.character import CharacterRead, CharacterUpdate, VoiceOption
from app.services import media
from app.services.character_look import derive_age_group
from app.services.voice_profile import (
    AGE_GROUPS,
    GENDERS,
    VOICE_LABEL,
    VOICE_PITCH_HZ,
    normalize_age_group,
    normalize_gender,
    profile_for,
)

router = APIRouter(prefix="/characters", tags=["characters"])


@router.get("/voices", response_model=list[VoiceOption], summary="可选音色（含实测基频，供选角）")
def list_voices() -> list[VoiceOption]:
    """列出可用的 TTS 音色。

    为什么要把基频也返回：我听不到音频，所以用自相关算法测了每个音色的音高
    （男声约 85~155Hz、女声约 165~255Hz）。前端把它显示出来，
    用户选音色时就有客观依据，不用一个一个试。
    """
    return [
        VoiceOption(voice=voice, label=label, pitch_hz=VOICE_PITCH_HZ.get(voice))
        for voice, label in VOICE_LABEL.items()
    ]


def _get_owned_character(db: DbSession, character_id: int, user: User) -> Character:
    """角色 -> 剧 -> 主人，一层层校验归属。"""
    character = db.get(Character, character_id)
    if character is None or character.drama.owner_id != user.id:
        raise NotFoundError(f"角色 {character_id} 不存在")
    return character


@router.patch("/{character_id}", response_model=CharacterRead, summary="修改角色卡（人设 + 音色 + 外貌）")
def update_character(
    character_id: int,
    payload: CharacterUpdate,
    db: DbSession,
    user: CurrentUser,
) -> Character:
    """修改角色。

    **两个字段之间存在联动**，这里必须显式处理，否则会出现别扭的结果：

    1. `age` → `age_group`：用户填的是具体年龄（28 岁），但音色矩阵是按年龄段选的。
       所以改年龄时要同步年龄段，否则会出现"28 岁却用老年音色"。
       如果调用方**同时明确传了** age_group，就以调用方为准（不覆盖用户的选择）。

    2. 性别/年龄段变了 → 重新推一套配音参数（除非调用方明确指定了音色），
       否则会出现"改成女性了，声音还是男声"。

    `look_prompt` 的规则：**留空 = 每次从角色卡自动生成；填了 = 以它为准**。
    所以如果你手写了 look_prompt，之后又改了外貌，画面不会自动跟着变 ——
    想同步就把 look_prompt 清空（这是刻意的取舍：手写的优先级必须高于自动生成）。
    """
    character = _get_owned_character(db, character_id, user)

    old_gender, old_age_group = character.gender, character.age_group

    # ── 人设（同时影响配音和画面）──
    if payload.gender is not None:
        character.gender = normalize_gender(payload.gender)

    if payload.age is not None:
        character.age = payload.age
    if payload.age_group is not None:
        character.age_group = normalize_age_group(payload.age_group)
    elif payload.age is not None:
        # 只给了年龄：年龄段跟着年龄推导
        derived = derive_age_group(payload.age)
        if derived:
            character.age_group = derived

    if payload.personality is not None:
        character.personality = payload.personality[:500]
    if payload.notes is not None:
        character.notes = payload.notes[:500]

    # ── 角色卡：给画面用的字段 ──
    if payload.appearance is not None:
        character.appearance = payload.appearance[:500]
    if payload.occupation is not None:
        character.occupation = payload.occupation[:60]
    if payload.background is not None:
        character.background = payload.background[:2000]
    if payload.look_prompt is not None:
        character.look_prompt = payload.look_prompt[:500]

    # ── 配音参数：人设变了就重推，除非明确指定了音色 ──
    human_changed = character.gender != old_gender or character.age_group != old_age_group
    if payload.voice is None and human_changed:
        profile = profile_for(character.gender, character.age_group)
        character.voice = profile.voice
        character.pitch_shift = profile.pitch_shift
        character.speed = profile.speed

    if payload.voice is not None:
        if payload.voice not in VOICE_LABEL:
            raise NotFoundError(f"音色 {payload.voice} 不存在")
        character.voice = payload.voice
    if payload.pitch_shift is not None:
        character.pitch_shift = payload.pitch_shift
    if payload.speed is not None:
        character.speed = payload.speed

    if not character.voice:  # 兜底，防止出现空音色导致合成失败
        character.voice = profile_for(character.gender, character.age_group).voice

    db.commit()
    db.refresh(character)
    return character


@router.get("/{character_id}/turnaround", summary="读取角色三视图（需登录，且只能读自己的）")
def get_character_turnaround(character_id: int, db: DbSession, user: CurrentUser) -> FileResponse:
    """角色三视图（正面/侧面/背面并排的设定图）。

    和镜头画面一样走带鉴权的接口，不挂公开静态目录 ——
    角色设定图属于用户的创作内容。
    """
    character = _get_owned_character(db, character_id, user)

    if not character.turnaround_path or not media.exists(character.turnaround_path):
        raise NotFoundError("这个角色还没有三视图")

    return FileResponse(
        media.absolute_path(character.turnaround_path),
        # 按文件真实格式给 Content-Type（智谱返回 JPEG，写死 png 是错的）
        media_type=media.content_type_for(character.turnaround_path),
        headers={"Cache-Control": "no-cache"},
    )


__all__ = ["router", "GENDERS", "AGE_GROUPS"]
