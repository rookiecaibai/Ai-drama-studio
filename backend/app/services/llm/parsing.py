"""大模型输出解析工具。

放在这里而不是 script_service 里，是因为现在有两个场景需要它：
生成分镜、生成大纲。公共逻辑抽出来，避免复制粘贴。
"""

import json
import re

from app.core.errors import AppError


def extract_json(text: str) -> dict:
    """从模型输出里抠出 JSON。

    现实情况：即使我们要求"只输出 JSON"，模型仍可能包一层 ```json``` 或加一句开场白，
    所以必须有容错解析。这是 AI 应用开发和传统后端最大的差异之一：
    上游返回是"概率性"的，代码必须假设它会不规范。
    """
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    raw = fenced.group(1) if fenced else text

    start, end = raw.find("{"), raw.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise AppError("大模型没有返回可解析的 JSON", code="llm_bad_output", status_code=502)

    try:
        data = json.loads(raw[start : end + 1])
    except json.JSONDecodeError as exc:
        raise AppError(f"大模型返回的 JSON 解析失败: {exc}", code="llm_bad_output", status_code=502) from exc

    if not isinstance(data, dict):
        raise AppError("大模型返回的 JSON 顶层不是对象", code="llm_bad_output", status_code=502)
    return data
