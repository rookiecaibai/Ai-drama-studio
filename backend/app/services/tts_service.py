"""逐句配音 + 拼接成镜头音频。

**这一版和最初版本的区别（为什么重写）**
最初的实现是"一个镜头一段音频、一个音色读到底"，它会犯三个错：
    ① 把"林晚："这个角色名也念出来；
    ② 一个镜头里两个人对话，却是同一个声音；
    ③ 不管什么情境，语气语速都一样。
现在的流程：
    台词解析 -> 角色分配 -> 情绪分析 -> 逐句合成（各自的音色/语速/音高）-> 拼接

**为什么要"逐句合成再拼接"而不是整段一次合成**
因为一句话的音色、语速是要分别控制的。
拼接用 ffmpeg 的 concat 滤镜（会重采样对齐，所以各段格式不同也能拼），
句与句之间插一小段静音 —— 否则两句会紧贴着，听起来像一口气说完。

**变调解决"男声只有一个"的问题**：见 app/services/voice_profile.py 的说明。
"""

import logging
import uuid
from collections.abc import Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.db.models import Character, DialogueLine, Drama, Episode, Shot
from app.services import audio_fx, media
from app.services.cast_service import analyze_cast
from app.services.dialogue import parse_dialogue
from app.services.emotion_service import analyze_emotions
from app.services.llm.base import LLMProvider
from app.services.tts.base import TtsProvider
from app.services.voice_profile import DEFAULT_VOICE, is_narrator, profile_for

logger = logging.getLogger(__name__)


def sync_dialogue_lines(db: Session, shot: Shot) -> list[DialogueLine]:
    """把镜头里的台词文本解析成逐句记录（幂等：重复调用结果一致）。

    只在**内容变了**的时候重建，避免每次配音都把用户看到过的情绪标签清掉。
    """
    parsed = parse_dialogue(shot.dialogue)
    existing = list(shot.lines)

    same = len(existing) == len(parsed) and all(
        row.text == line.text and row.speaker == (line.speaker or "")
        for row, line in zip(existing, parsed, strict=True)
    )
    if same:
        return existing

    for row in existing:
        db.delete(row)
    db.flush()

    created: list[DialogueLine] = []
    for index, line in enumerate(parsed):
        row = DialogueLine(
            shot_id=shot.id,
            index=index,
            speaker=line.speaker or "",
            text=line.text,
            cue=line.cue or "",
            speed=None,  # 还没做情绪分析，语速交给角色档案决定
        )
        db.add(row)
        created.append(row)
    db.commit()
    for row in created:
        db.refresh(row)
    return created


def load_lines_by_shot(db: Session, shot_ids: list[int]) -> dict[int, list[DialogueLine]]:
    """按镜头显式查出台词。

    **这里踩过一个坑，值得记下来**：
    一开始我用 `select(Shot).options(selectinload(Shot.lines))` 拿台词，
    但那个集合在"创建台词之前"就已经被加载过一次（当时是空的）。
    SQLAlchemy 对**已加载**的关系不会再被后续查询覆盖（除非 populate_existing），
    结果是：台词明明已经写进库了，内存里看到的还是空列表 ——
    角色分配、情绪分析全部静默跳过，配音退化成"一个音色读到底"。

    教训：**后台任务里的"我刚写的数据"要显式查一次**，
    不要依赖 ORM 关系集合的缓存状态。显式查询慢不了多少，但不会骗你。
    """
    if not shot_ids:
        return {}
    rows = db.scalars(
        select(DialogueLine)
        .where(DialogueLine.shot_id.in_(shot_ids))
        .order_by(DialogueLine.shot_id, DialogueLine.index)
    )
    result: dict[int, list[DialogueLine]] = {}
    for row in rows:
        result.setdefault(row.shot_id, []).append(row)
    return result


def assign_characters(
    db: Session, *, drama_id: int, lines: list[DialogueLine]
) -> list[Character]:
    """把每一句台词对应到角色（按说话人名字匹配）。"""
    characters = list(
        db.scalars(select(Character).where(Character.drama_id == drama_id).order_by(Character.id))
    )
    by_name = {character.name: character for character in characters}
    narrator = next((c for c in characters if is_narrator(c.name)), None)

    for line in lines:
        if line.character_id:
            continue
        character = by_name.get(line.speaker)
        if character is None and not line.speaker:
            character = narrator  # 没写说话人的句子按旁白处理
        if character is not None:
            line.character_id = character.id
    db.commit()
    return characters


def _profile_of(character: Character | None):
    if character is None:
        return profile_for(None, None)
    if character.voice:
        from app.services.voice_profile import VoiceProfile

        return VoiceProfile(character.voice, character.pitch_shift, character.speed, character.name)
    return profile_for(character.gender, character.age_group)


async def _synthesize_line(
    *,
    line: DialogueLine,
    character: Character | None,
    provider: TtsProvider,
    work_dir,
    index: int,
) -> None:
    """合成单句，并把结果（含变调）写到最终路径。"""
    profile = _profile_of(character)
    raw_path = work_dir / f"raw_{index:03d}.wav"

    data = await provider.synthesize(
        text=line.text,
        style="",
        voice=profile.voice or DEFAULT_VOICE,
        # line.speed 有值说明情绪分析算过了；否则用角色自己的基础语速
        speed=line.speed if line.speed is not None else profile.speed,
    )
    if not data:
        raise AppError("配音服务返回了空音频", code="tts_bad_output", status_code=502)
    raw_path.write_bytes(data)

    rel_path = media.line_audio_rel_path(line.id)
    destination = media.absolute_path(rel_path)
    destination.parent.mkdir(parents=True, exist_ok=True)

    # 变调（时长不变）。pitch_shift 接近 0 时 shift_pitch 内部直接复制，不额外编码。
    previous = line.audio_path
    audio_fx.shift_pitch(raw_path, destination, profile.pitch_shift)
    if previous and previous != rel_path:
        media.delete(previous)

    line.audio_path = rel_path
    line.audio_error = None


async def generate_audio_for_episode(
    db: Session,
    *,
    episode: Episode,
    provider: TtsProvider,
    only_missing: bool = True,
    on_total: Callable[[int], None] | None = None,
    on_progress: Callable[[], None] | None = None,
    llm: LLMProvider | None = None,
) -> tuple[int, int]:
    """返回 (计划配音的镜头数, 成功数)。

    llm 传了才会做"角色分析 + 情绪分析"；不传就退化成"整段一个音色"的老行为
    （测试和离线场景用得上）。
    """
    shots = list(
        db.scalars(
            select(Shot).where(Shot.episode_id == episode.id).order_by(Shot.number)
        )
    )
    if not shots:
        raise AppError("这一集还没有分镜，请先生成分镜", code="no_shots", status_code=400)

    # ① 解析台词（这一步不花钱，先做）
    for shot in shots:
        sync_dialogue_lines(db, shot)

    # ② 显式重查台词 —— 不能用 shot.lines，原因见 load_lines_by_shot 的说明
    lines_by_shot = load_lines_by_shot(db, [shot.id for shot in shots])

    drama = db.get(Drama, episode.drama_id)

    # ③ 角色表 + 情绪分析（都要调大模型，所以只在传了 llm 时做）
    #
    # 这两步**失败也不能中断配音**：配音的主体是"把文字变成声音"，
    # 角色/情绪只是让它更贴切。上游模型抖一下就让整集配音失败，
    # 用户会觉得"这功能太脆"。失败就退回默认音色和语速，照常配完。
    if llm is not None:
        try:
            existing = list(db.scalars(select(Character).where(Character.drama_id == episode.drama_id)))
            if not existing:
                logger.info("剧 %s 还没有角色表，先分析一次", episode.drama_id)
                existing = await analyze_cast(db, drama=drama, provider=llm)

            all_lines = [line for lines in lines_by_shot.values() for line in lines]
            characters = assign_characters(db, drama_id=episode.drama_id, lines=all_lines)

            # 只给"还没分析过情绪"的镜头做分析 —— 重跑配音不该重复花这笔钱。
            # 但**整个镜头的台词一起送**（而不是只送没分析过的那几句）：
            # 否则提示词里的编号会从 0 跳到 2，模型很容易对错行。
            entries = [
                (shot, lines_by_shot[shot.id])
                for shot in shots
                if any(not line.emotion for line in lines_by_shot.get(shot.id, []))
            ]
            if entries:
                await analyze_emotions(
                    db,
                    drama=drama,
                    episode=episode,
                    entries=entries,
                    characters=characters,
                    provider=llm,
                )
        except Exception as exc:  # noqa: BLE001
            logger.warning("角色表/情绪分析失败，本次改用默认音色与语速继续配音：%s", exc)
            db.rollback()
            # 回滚会让内存里的对象过期，重新读一次台词（见 load_lines_by_shot 的说明）
            lines_by_shot = load_lines_by_shot(db, [shot.id for shot in shots])

    with_lines = [shot for shot in shots if lines_by_shot.get(shot.id)]
    targets = [shot for shot in with_lines if not (only_missing and shot.audio_path)]
    if on_total is not None:
        on_total(len(targets))

    work_dir = media.build_work_dir(f"tts-{episode.id}-{uuid.uuid4().hex[:8]}")
    silence = work_dir / "gap.wav"
    characters_by_id = {
        character.id: character
        for character in db.scalars(select(Character).where(Character.drama_id == episode.drama_id))
    }

    succeeded = 0
    try:
        for shot in targets:
            lines = sorted(lines_by_shot.get(shot.id, []), key=lambda row: row.index)
            gaps = audio_fx.gap_seconds([len(line.text) for line in lines])
            audio_fx.write_silence(silence, gaps)

            segments: list = []
            failed = 0
            for index, line in enumerate(lines):
                if not line.text.strip():
                    continue
                try:
                    await _synthesize_line(
                        line=line,
                        character=characters_by_id.get(line.character_id or -1),
                        provider=provider,
                        work_dir=work_dir,
                        index=index,
                    )
                except Exception as exc:  # noqa: BLE001 —— 单句失败不该毁掉整个镜头
                    line.audio_error = str(exc)[:300]
                    failed += 1
                    logger.warning("镜头 %s 第 %s 句配音失败: %s", shot.id, index, exc)
                    db.commit()
                    continue
                db.commit()
                if segments:
                    segments.append(silence)  # 句间停顿
                segments.append(media.absolute_path(line.audio_path))

            if not segments:
                # 把第一句的真实原因带上：只写"都没成功"等于没说，
                # 用户需要知道到底是超时、欠费还是音色不对。
                reasons = [line.audio_error for line in lines if line.audio_error]
                shot.audio_error = (
                    f"这个镜头的台词都没能合成成功：{reasons[0]}"
                    if reasons
                    else "这个镜头没有可朗读的台词"
                )
                db.commit()
                if on_progress is not None:
                    on_progress()
                continue

            rel_path = media.shot_audio_rel_path(shot.id, "wav")
            destination = media.absolute_path(rel_path)
            destination.parent.mkdir(parents=True, exist_ok=True)
            try:
                if len(segments) == 1:
                    destination.write_bytes(segments[0].read_bytes())
                else:
                    audio_fx.concat_audio(segments, destination)
            except Exception as exc:  # noqa: BLE001
                shot.audio_error = f"拼接失败: {str(exc)[:200]}"
                db.commit()
                if on_progress is not None:
                    on_progress()
                continue

            previous = shot.audio_path
            shot.audio_path = rel_path
            shot.audio_error = None
            if previous and previous != rel_path:
                media.delete(previous)
            succeeded += 1
            db.commit()
            if on_progress is not None:
                on_progress()
    finally:
        import shutil

        shutil.rmtree(work_dir, ignore_errors=True)

    logger.info(
        "剧集 %s 配音结束：有台词 %s 个镜头，计划处理 %s 个，成功 %s 个",
        episode.id,
        len(with_lines),
        len(targets),
        succeeded,
    )
    return len(targets), succeeded
