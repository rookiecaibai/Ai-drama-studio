"""厂商私有参数的解析（把配置里的 JSON 字符串变成字典）。

**为什么要有这个东西**
每家厂商都有自己的一套私有参数（智谱的 `watermark_enabled`、尺寸参数叫 size 还是 image_size……）。
如果每遇到一个就改一次代码，适配器会慢慢长满 if 分支。
做成"配置里写 JSON"之后，换厂商/调参数都不用动代码 —— 这和 IMAGE_SIZE_PARAM
是同一个思路，只是更通用。

**容错原则**：配置写错了（不是合法 JSON、不是对象）不能把服务搞崩，
记一条警告、当作没配，照常发请求。
"""

import json
import logging

logger = logging.getLogger(__name__)


def parse_extra_params(raw: str, *, name: str) -> dict:
    """把 '{"watermark_enabled": false}' 解析成 dict；解析不了就返回空字典。"""
    text = (raw or "").strip()
    if not text:
        return {}
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        logger.warning("%s 不是合法的 JSON，已忽略：%s（%s）", name, text[:120], exc)
        return {}
    if not isinstance(data, dict):
        logger.warning("%s 必须是一个 JSON 对象，已忽略：%s", name, text[:120])
        return {}
    return data
