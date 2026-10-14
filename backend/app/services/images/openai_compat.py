"""真实的文生图适配器（OpenAI 兼容协议）。

各家厂商的图片接口只在细节上有差异，主干是一样的：
    POST {base_url}/images/generations
    { "model": ..., "prompt": ..., "<尺寸参数名>": "768x1344" }
    返回 { "data": [ { "url": "..." } ] } 或 { "data": [ { "b64_json": "..." } ] }

已知差异（用配置项吸收，不改代码）：
- 尺寸参数名：OpenAI / 智谱叫 size，硅基流动叫 image_size -> IMAGE_SIZE_PARAM
- 返回结构：少数服务用 images[0].url 而不是 data[0].url -> 这里做了兼容解析

拿到 URL 后**立刻下载成字节**：厂商给的 URL 往往是临时地址，过期就失效，
所以我们的原则是"URL 用完即弃，图片存在自己磁盘上"。
"""

import base64
import logging

import httpx

from app.core.errors import AppError, LLMError
from app.services.images.base import ImageProvider

logger = logging.getLogger(__name__)


def _extract_image_ref(payload: dict) -> str:
    """从厂商返回里取出"图片在哪"，兼容几种常见结构。"""
    candidates: list[object] = []

    data = payload.get("data")
    if isinstance(data, list) and data:
        first = data[0]
        if isinstance(first, dict):
            candidates += [first.get("url"), first.get("b64_json"), first.get("image_url")]
    images = payload.get("images")
    if isinstance(images, list) and images:
        first = images[0]
        if isinstance(first, dict):
            candidates += [first.get("url"), first.get("b64_json")]
        elif isinstance(first, str):
            candidates.append(first)
    candidates += [payload.get("url"), payload.get("image_url")]

    for item in candidates:
        if isinstance(item, str) and item.strip():
            return item.strip()

    raise AppError(
        f"文生图服务返回的结构无法识别: {str(payload)[:200]}",
        code="image_bad_output",
        status_code=502,
    )


class OpenAICompatImageProvider(ImageProvider):
    name = "openai_compat"

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model: str,
        size: str,
        size_param: str = "size",
        timeout: float = 120.0,
    ):
        if not api_key:
            raise LLMError(
                "未配置 IMAGE_API_KEY，请把 IMAGE_PROVIDER 设为 mock 或填入真实 Key",
                code="image_not_configured",
                status_code=500,
            )
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._size = size
        self._size_param = size_param
        self._timeout = timeout

    async def generate(self, *, prompt: str) -> bytes:
        payload = {
            "model": self._model,
            "prompt": prompt,
            self._size_param: self._size,
        }
        headers = {"Authorization": f"Bearer {self._api_key}"}

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            try:
                resp = await client.post(f"{self._base_url}/images/generations", json=payload, headers=headers)
                resp.raise_for_status()
            except httpx.HTTPStatusError as exc:
                raise LLMError(
                    f"文生图服务返回错误状态 {exc.response.status_code}: {exc.response.text[:200]}",
                    code="image_generation_failed",
                ) from exc
            except httpx.HTTPError as exc:
                raise LLMError(f"调用文生图服务失败: {exc}", code="image_generation_failed") from exc

            data = resp.json()
            ref = _extract_image_ref(data)

            # 情况一：返回 base64 图片数据
            if not ref.startswith("http"):
                try:
                    return base64.b64decode(ref)
                except Exception as exc:  # noqa: BLE001
                    raise AppError("文生图返回的内容既不是 URL 也不是合法 base64", code="image_bad_output", status_code=502) from exc

            # 情况二：返回临时 URL —— 立刻下载下来，避免过期
            try:
                file_resp = await client.get(ref)
                file_resp.raise_for_status()
            except httpx.HTTPError as exc:
                raise LLMError(f"图片已生成但下载失败: {exc}", code="image_download_failed") from exc

        logger.info("文生图完成，图片大小 %s 字节", len(file_resp.content))
        return file_resp.content
