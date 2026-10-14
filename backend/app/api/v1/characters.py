"""角色表接口：查音色、改角色。"""

from fastapi import APIRouter

from app.api.deps import CurrentUser, DbSession
from app.core.errors import NotFoundError
from app.db.models import Character, User
from app.schemas.character import CharacterRead, CharacterUpdate, VoiceOption
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


@router.patch("/{character_id}", response_model=CharacterRead, summary="修改角色（性别/年龄/音色/语速/音调）")
def update_character(
    character_id: int,
    payload: CharacterUpdate,
    db: DbSession,
    user: CurrentUser,
) -> Character:
    character = _get_owned_character(db, character_id, user)

    if payload.gender is not None:
        character.gender = normalize_gender(payload.gender)
    if payload.age_group is not None:
        character.age_group = normalize_age_group(payload.age_group)
    if payload.personality is not None:
        character.personality = payload.personality[:500]
    if payload.notes is not None:
        character.notes = payload.notes[:500]

    # 只改了性别/年龄、没指定音色时，按新的人设重新推一套配音参数 ——
    # 否则会出现"改成女性了，声音还是男声"这种别扭的结果。
    reassign = payload.voice is None and (payload.gender is not None or payload.age_group is not None)
    if reassign:
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


__all__ = ["router", "GENDERS", "AGE_GROUPS"]
