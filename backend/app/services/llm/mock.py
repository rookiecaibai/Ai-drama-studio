"""本地假模型：不联网、不花钱、结果稳定。

它的价值不是"假装有 AI"，而是：
- 让你在拿到 API Key 之前就能把「接口 -> 服务 -> 数据库 -> 前端」整条链路调试通；
- 让自动化测试不受外部网络和模型随机性影响（否则测试会随机失败）。

**它要能回答三类不同的提示词**（分镜 / 角色表 / 情绪分析），
因为配音任务会依次用到这三类。如果只认分镜那一种，
在没有 API Key 的环境下"多角色配音"整条链路就演示不出来。
"""

import hashlib
import json
import re

from app.services.llm.base import LLMProvider

#: mock 用的分镜脚本。刻意写成**多角色对白**，
#: 这样在 mock 模式下也能演示"一个镜头里两个人用不同音色说话"。
MOCK_SHOTS = [
    {
        "shot_type": "特写",
        "camera": "缓慢推镜",
        "description": "雨夜里，林晚握着手机站在便利店门口，屏幕亮光照亮她发红的眼眶。",
        "dialogue": "（旁白）那天之后，我再也没见过他。",
        "duration_sec": 3.5,
        "image_prompt": "cinematic close-up, young woman, rainy night, convenience store, neon reflection, 35mm film",
    },
    {
        "shot_type": "全景",
        "camera": "固定机位",
        "description": "城市霓虹在湿地面上拉出长长的反光，行人匆匆掠过画面。",
        "dialogue": "",
        "duration_sec": 2.5,
        "image_prompt": "wide shot, rainy city street at night, neon reflections, shallow depth of field",
    },
    {
        "shot_type": "中景",
        "camera": "跟拍",
        "description": "陈默撑伞快步走来，在林晚身后三步远停下。",
        "dialogue": "陈默：我找了你好久。",
        "duration_sec": 4.0,
        "image_prompt": "medium shot, man with umbrella walking fast, backlit street lamp, moody",
    },
    {
        "shot_type": "特写",
        "camera": "手持微晃",
        "description": "林晚猛地回头，雨水顺着发梢滴落。",
        "dialogue": "林晚：你还来干什么？",
        "duration_sec": 3.0,
        "image_prompt": "close-up, woman turning head, rain drops, emotional, cinematic lighting",
    },
    {
        "shot_type": "近景",
        "camera": "正反打",
        "description": "两人对视，雨声把周围的车流声压了下去。",
        "dialogue": "林晚：你到底想干什么？\n陈默：先跟我走，这里不安全。",
        "duration_sec": 5.0,
        "image_prompt": "two shot, confrontation, blurred traffic lights background, dramatic",
    },
    {
        "shot_type": "特写",
        "camera": "拉镜",
        "description": "陈默把一张对折的纸条塞进她手心，转身走进雨里。",
        "dialogue": "（旁白）那张纸上，写着一个地址。",
        "duration_sec": 3.5,
        "image_prompt": "extreme close-up, hand passing folded note, rain, night, cinematic",
    },
]


class MockProvider(LLMProvider):
    name = "mock"

    async def complete(self, *, system: str, user: str) -> str:
        if "选角表" in system:
            return json.dumps({"characters": self._cast(user)}, ensure_ascii=False, indent=2)
        if "情绪和语速" in system:
            return json.dumps({"lines": self._emotions(user)}, ensure_ascii=False, indent=2)
        payload = {
            "title": "雨夜便利店",
            "synopsis": "雨夜的便利店门口，林晚等来一个不该出现的人，并收到一张写着地址的纸条。",
            "shots": MOCK_SHOTS,
        }
        return json.dumps(payload, ensure_ascii=False, indent=2)

    # ───────────────────────── 角色表 ─────────────────────────

    @staticmethod
    def _cast(user: str) -> list[dict]:
        """按提示词里给出的说话人名单生成角色。

        性别/年龄由名字的哈希决定 —— 这样**同一个名字永远得到同一套设定**，
        测试不会因为随机数而忽上忽下。
        """
        characters: list[dict] = []
        for name in MockProvider._speakers(user):
            if any(token in name for token in ("旁白", "画外音", "解说")):
                characters.append(
                    {
                        "name": name,
                        "gender": "female",
                        "age_group": "middle",
                        "personality": "叙述者，语气平静克制",
                        "role": "旁白",
                    }
                )
                continue
            digest = hashlib.sha256(name.encode("utf-8")).digest()
            characters.append(
                {
                    "name": name,
                    "gender": ("male", "female")[digest[0] % 2],
                    "age_group": ("young", "middle", "elderly", "teen")[digest[1] % 4],
                    "personality": "（mock 自动生成：只为了演示不同音色，不代表真实人设）",
                    "role": "配角",
                }
            )
        return characters

    @staticmethod
    def _speakers(user: str) -> list[str]:
        names: list[str] = []
        collecting = False
        for raw in user.splitlines():
            if "出现过的说话人" in raw:
                collecting = True
                continue
            if not collecting:
                continue
            stripped = raw.strip()
            if not stripped:
                break
            if "：" in stripped:
                names.append(stripped.split("：", 1)[0].strip())
        return names

    # ───────────────────────── 情绪分析 ─────────────────────────

    @staticmethod
    def _emotions(user: str) -> list[dict]:
        """按台词本身的标点和用词猜情绪 —— 简单规则，但结果稳定且看得出差别。"""
        results: list[dict] = []
        shot_number = 0
        for raw in user.splitlines():
            shot_match = re.match(r"镜头\s*(\d+)", raw.strip())
            if shot_match:
                shot_number = int(shot_match.group(1))
                continue

            line_match = re.match(r"\s*line\s+(\d+)｜(.+?)：(.*)$", raw)
            if not line_match or not shot_number:
                continue

            index = int(line_match.group(1))
            speaker = line_match.group(2).strip()
            text = line_match.group(3).strip()
            emotion, intensity, pace = MockProvider._guess(speaker, text)
            results.append(
                {
                    "shot": shot_number,
                    "line": index,
                    "speaker": speaker,
                    "emotion": emotion,
                    "intensity": intensity,
                    "pace": pace,
                }
            )
        return results

    @staticmethod
    def _guess(speaker: str, text: str) -> tuple[str, float, str]:
        if any(token in speaker for token in ("旁白", "画外音")):
            return "平静", 0.4, "slow"
        if "……" in text:
            return "犹豫", 0.6, "slow"
        if "！" in text:
            return "急迫", 0.85, "fast"
        if "？" in text:
            return "紧张", 0.65, "normal"
        return "平静", 0.5, "normal"
