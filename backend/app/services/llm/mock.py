"""本地假模型：不联网、不花钱、结果稳定。

它的价值不是"假装有 AI"，而是：
- 让你在拿到 API Key 之前就能把「接口 -> 服务 -> 数据库 -> 前端」整条链路调试通；
- 让自动化测试不受外部网络和模型随机性影响（否则测试会随机失败）。
"""

import json

from app.services.llm.base import LLMProvider


class MockProvider(LLMProvider):
    name = "mock"

    async def complete(self, *, system: str, user: str) -> str:
        shots = [
            {
                "shot_type": "特写",
                "camera": "缓慢推镜",
                "description": "雨夜里，女主握着手机站在便利店门口，屏幕亮光照亮她发红的眼眶。",
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
                "description": "男主撑伞快步走来，在女主身后三步远停下。",
                "dialogue": "我找了你好久。",
                "duration_sec": 4.0,
                "image_prompt": "medium shot, man with umbrella walking fast, backlit street lamp, moody",
            },
            {
                "shot_type": "特写",
                "camera": "手持微晃",
                "description": "女主猛地回头，雨水顺着发梢滴落。",
                "dialogue": "你还来干什么？",
                "duration_sec": 3.0,
                "image_prompt": "close-up, woman turning head, rain drops, emotional, cinematic lighting",
            },
            {
                "shot_type": "近景",
                "camera": "正反打",
                "description": "两人对视，沉默两秒，背景的车流声渐渐压低。",
                "dialogue": "",
                "duration_sec": 2.0,
                "image_prompt": "two shot, confrontation, blurred traffic lights background, dramatic",
            },
            {
                "shot_type": "特写",
                "camera": "拉镜",
                "description": "男主把一张对折的纸条塞进她手心，转身走进雨里。",
                "dialogue": "（旁白）那张纸上，写着一个地址。",
                "duration_sec": 3.5,
                "image_prompt": "extreme close-up, hand passing folded note, rain, night, cinematic",
            },
        ]

        payload = {
            "title": "雨夜便利店",
            "synopsis": "雨夜的便利店门口，女主等来一个不该出现的人，并收到一张写着地址的纸条。",
            "shots": shots,
        }
        return json.dumps(payload, ensure_ascii=False, indent=2)
