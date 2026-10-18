"""角色怎么变成"画面上的文字描述"—— 全是纯函数，不含任何 I/O。

为什么单独一个模块、而且坚持纯函数：
"角色一致性"最容易出问题的地方不是调用模型，而是**提示词拼得对不对**
（角色有没有被算进来、外貌描述有没有漏、长度有没有超）。这部分逻辑用纯函数写，
就能用一个普通单测固定住，不用起数据库、不用联网。

**这个模块解决的核心问题**：
文生图模型没有记忆，每次调用都是全新生成。所以同一个角色要在不同镜头里长得像，
唯一可靠的办法是：把"这个人长什么样"固定成一段文字（look_prompt），
画每个镜头时都把它带上。这是最朴素、但目前**唯一能做到**的一致性手段
（详见 ImageProvider.supports_reference 的说明：智谱的画图模型不吃参考图）。
"""

from app.db.models import Character, Shot
from app.services.voice_profile import is_narrator

#: 年龄段 -> 中文（年龄是给模型看的，年龄段是给音色矩阵用的）
AGE_GROUP_TEXT = {
    "child": "儿童",
    "teen": "少年",
    "young": "青年",
    "middle": "中年",
    "elderly": "老年",
}

GENDER_TEXT = {"male": "男性", "female": "女性"}

#: 单个角色的外貌描述上限（防止一个角色把整段提示词撑爆）
MAX_LOOK_LEN = 200
#: 合并后的整段提示词上限。glm-image 的上限是 1000 字符，留点余量。
MAX_PROMPT_LEN = 900


def derive_age_group(age: int | None) -> str | None:
    """把具体年龄映射到年龄段。

    为什么需要这个转换：音色矩阵（voice_profile）是按**年龄段**选音色的
    （年轻男/中年男/年老男 的声线不同），而角色卡上用户填的是**具体年龄**。
    所以年龄变了，年龄段要跟着走，否则会出现"28 岁却用老年音色"。
    """
    if age is None:
        return None
    if age < 13:
        return "child"
    if age < 20:
        return "teen"
    if age < 40:
        return "young"
    if age < 60:
        return "middle"
    return "elderly"


def build_look_prompt(character: Character) -> str:
    """从角色卡拼出一段"这个人长什么样"的描述。

    只放**画面相关的信息**（性别/年龄/职业/外貌）。
    背景故事（background）刻意不放：它对画面没有直接帮助，反而会稀释提示词里的
    视觉信息 —— 模型可能真的按故事去画"悲惨的过去"之类抽象的东西。
    """
    parts: list[str] = []

    gender = GENDER_TEXT.get(character.gender or "")
    if gender:
        parts.append(gender)

    if character.age:
        parts.append(f"{character.age}岁")
    else:
        age_group = AGE_GROUP_TEXT.get(character.age_group or "")
        if age_group:
            parts.append(age_group)

    if character.occupation and character.occupation.strip():
        parts.append(character.occupation.strip())

    text = "，".join(parts)
    appearance = (character.appearance or "").strip()
    if appearance:
        text = f"{text}，外貌：{appearance}" if text else f"外貌：{appearance}"
    return text[:MAX_LOOK_LEN]


def resolve_look_prompt(character: Character) -> str:
    """最终用哪段文字来描述这个角色。

    **留空 = 自动从角色卡生成；填了 = 以用户填的为准**。
    这样用户既有"改完字段自动同步"的省心，也能手写一段更精准的锚定描述
    （比如指定某个具体演员的长相）。
    """
    override = (character.look_prompt or "").strip()
    if override:
        return override[:MAX_LOOK_LEN]
    return build_look_prompt(character)


#: 剧集风格里出现这些词，就认为用户要的就是动漫/插画风
_ANIME_HINTS = ("动漫", "插画", "二次元", "漫画", "anime", "cartoon")


def _style_lock(style: str) -> str:
    """画风锁定指令。

    **这是实测踩出来的**：只写"角色三视图设定图"，模型会画成插画/二次元风，
    而这个剧的镜头画面是按"电影感"出的**写实摄影风** —— 两边风格对不上，
    设定图就失去了"当作长相参考"的意义。
    所以默认显式要求写实；只有当剧集风格本身就要动漫时才不干预。
    """
    if any(hint in (style or "") for hint in _ANIME_HINTS):
        return "画风：与剧集一致的插画风格，线条干净。"
    return "画风：写实摄影质感，真实的皮肤与布料质感；不要插画、不要动漫、不要二次元。"


def build_sheet_prompt(character: Character, *, style: str = "") -> str:
    """角色三视图（设定图）的提示词。

    每条要求都是必要的，少了就会出问题（下面几条都是实测出来的）：
    1. **把三个视角的朝向逐个写清楚** —— 只说"正面/侧面/背面"，模型会给
       "侧面/侧斜/正面"，**背面根本画不出来**；
    2. **纯色背景 + 全身入镜** —— 否则背景干扰，看不清角色本身的形体；
    3. **"禁止文字/水印"** —— 智谱的模型很爱在图上加字，加了就毁了设定图；
    4. **锁定写实画风** —— 否则会画成插画，和镜头画面的写实风格对不上（见 _style_lock）；
    5. **带上剧集风格** —— 保证设定图和这个剧的镜头画面是同一个世界。
    """
    look = resolve_look_prompt(character)
    name = character.name or "角色"
    style_tag = "插画风格" if any(h in (style or "") for h in _ANIME_HINTS) else "写实摄影风格"

    # ⚠️ 这段措辞是**实测五个版本后选出来的**，别随手改。记录一下抽奖过程：
    #
    #   v1  三个视角一句话说完                    -> 三视角全身 ✅ / 插画风 ❌
    #   v2  视角展开写"能看到脸""看到后脑"        -> 头肩特写 ❌ / 全身没了
    #   v3  画风前置 + 视角单独一行               -> 拼贴画 ❌ / 只出现一个侧身
    #   v4  "写实摄影风格"打头 + 视角同一句        -> 写实风 ✅ / 但只画了一个人 ❌
    #   v5（现在这个）三视角打头 + 画风塞括号     -> 同时保住两个要求的成功率最高
    #
    # 结论：这类模型对"一张图里排三个视角"的**版面控制本身就不稳定**，
    # 继续调提示词只是在抽奖。所以产品上的解法是：
    #   ① 给「重画三视图」按钮 —— 不满意就再抽一次（cogview-3-flash 免费，几乎零成本）；
    #   ② 三视图只是"让用户看一眼并确认长相"的确认物，
    #      真正锁定各镜头长相的是 look_prompt 这段文字（它每张图都带）。
    lines = [
        f"角色三视图设定图：同一个角色（{name}）的正面、正侧面、背面三个全身视角，"
        f"从左到右并排画在同一张图里，从头到脚完整入镜（{style_tag}）。",
        f"角色设定：{look}。",
    ]
    if style:
        lines.append(f"整体氛围：{style}。")
    lines.append(
        "统一要求：白色纯色背景，均匀柔和布光，标准站立姿势，全身入镜，"
        "三个视角是同一个人的同一套发型和服装。"
    )
    lines.append(_style_lock(style))
    lines.append("禁止：文字、水印、logo、多余人像、背景杂物。")
    return "\n".join(lines)[:MAX_PROMPT_LEN]


def characters_in_shot(
    shot: Shot, characters: list[Character], lines: list | None = None
) -> list[Character]:
    """这个镜头里会出现哪些角色。

    两条依据取**并集**：
    ① 在这个镜头有台词的角色（台词行的 character_id）—— 最可靠；
    ② 名字出现在画面描述或台词里的角色 —— 覆盖"入镜但没说话"的情况
       （比如"林晚站在雨里，一言不发"）。

    **宁可多算一个，也不要漏掉**：多算的代价是多带一段外貌描述（无害）；
    漏掉的代价是"这个角色在这个镜头里没被描述过"，长相就会乱飘 ——
    而这正是我们要解决的问题本身。

    注意 lines 要由调用方**显式传入**：ORM 的关系集合一旦被加载过就不会刷新，
    直接读 shot.lines 可能拿到空列表（这个坑我们踩过，见 docs/04 的踩坑合集）。
    """
    if lines is None:
        lines = list(shot.lines or [])

    present_ids: set[int] = set()
    for line in lines:
        character_id = getattr(line, "character_id", None)
        if character_id:
            present_ids.add(character_id)

    text = f"{shot.description or ''} {shot.dialogue or ''}"
    for character in characters:
        # 旁白/画外音**没有形象**：把它算进"出场角色"只会往提示词里塞一句
        # 毫无意义的"女性，青年"，白白稀释画面信息。所以显式排除。
        if is_narrator(character.name):
            continue
        if character.name and character.name in text:
            present_ids.add(character.id)

    # 按传入顺序返回，保证提示词稳定（同一个镜头每次得到同样的提示词）
    return [c for c in characters if c.id in present_ids and not is_narrator(c.name)]


def compose_shot_prompt(
    shot: Shot, characters: list[Character], *, style: str = ""
) -> tuple[str, list[str]]:
    """把"镜头画面描述"和"出场角色的外貌"合并成最终提示词。

    返回 (提示词, 这次实际用到的角色名列表) —— 第二个值是为了让日志和接口能说清楚
    "这一张图画了谁"，排查时不用去猜。

    **顺序很关键：角色在前、场景在后。**
    文生图模型对提示词前半段更敏感，把"这个人长什么样"放在前面，
    比放在后面更能稳定地画出同一个人。
    """
    scene = (shot.image_prompt or shot.description or "").strip()

    looks: list[str] = []
    used_names: list[str] = []
    for character in characters:
        look = resolve_look_prompt(character)
        if look:
            looks.append(look)
            used_names.append(character.name)

    parts: list[str] = []
    if looks:
        parts.append("；".join(looks))
    if style and looks:
        parts.append(f"画面风格：{style}")
    if scene:
        parts.append(scene)

    prompt = "；".join(parts).strip()
    return prompt[:MAX_PROMPT_LEN], used_names
