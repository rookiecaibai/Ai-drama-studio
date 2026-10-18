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

import asyncio
import base64
import logging
import time
from pathlib import Path

import httpx

from app.core.errors import AppError, LLMError
from app.services.images.base import ImageProvider

logger = logging.getLogger(__name__)

#: 进程级"图片请求闸门"：同一时刻只允许一个图片请求在途。
#:
#: 为什么需要它（真实事故）：用户同时点了「生成三视图」和「生成画面」，
#: 两个独立的后台任务并发打同一个账户 -> 厂商直接 429（账户级速率限制），
#: 4 张图全失败。用自己的应用去撞自己的限流，是最没必要的一种失败。
_IMAGE_GATE = asyncio.Lock()
#: 上一次请求的时间戳，用来保证两次请求之间至少间隔 min_interval
_last_call_at: float = 0.0


class _TransientImageError(Exception):
    """可重试的错误（限流 / 5xx / 网络抖动）。"""

    def __init__(self, message: str, retry_after: float | None = None):
        super().__init__(message)
        self.message = message
        self.retry_after = retry_after


def _parse_retry_after(raw: str | None) -> float | None:
    """厂商如果给了 Retry-After（秒），就听它的，别自己猜。"""
    if not raw:
        return None
    try:
        return max(0.0, float(raw))
    except ValueError:
        return None


def _mark_called() -> None:
    global _last_call_at
    _last_call_at = time.monotonic()


async def _wait_for_interval(min_interval: float) -> None:
    """保证两次请求之间至少隔 min_interval 秒。"""
    if min_interval <= 0 or _last_call_at <= 0:
        return
    elapsed = time.monotonic() - _last_call_at
    if elapsed < min_interval:
        await asyncio.sleep(min_interval - elapsed)


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
    # 智谱的 cogview-3-flash / cogview-4 / glm-image 都是纯文本输入，不能吃参考图。
    # 换成支持 image-to-image 的厂商时，这里改成 True 并处理下面的 reference_image。
    supports_reference = False

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model: str,
        size: str,
        size_param: str = "size",
        timeout: float = 120.0,
        extra_params: dict | None = None,
        min_interval: float = 1.2,
        retry_attempts: int = 3,
        retry_backoff: float = 2.0,
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
        self._extra_params = extra_params or {}
        self._min_interval = max(0.0, min_interval)
        self._retry_attempts = max(1, retry_attempts)
        self._retry_backoff = max(0.1, retry_backoff)
        self._warned_no_reference = False

    async def generate(self, *, prompt: str, reference_image: Path | None = None) -> bytes:
        # 厂商不支持参考图时**只提示一次**（避免每张图刷一条同样的日志）。
        # 这里刻意不抛错：参考图是"锦上添花"，没有它也应该能出图 ——
        # 角色一致性由角色卡里的 look_prompt（文本描述）兜底。
        if reference_image is not None and not self._warned_no_reference:
            logger.info(
                "当前文生图模型 %s 不支持参考图，已退回纯文本描述（角色一致性靠 look_prompt）",
                self._model,
            )
            self._warned_no_reference = True

        # 先铺厂商私有参数，再用必需字段覆盖它们 ——
        # 这样即使配置里写错（比如把 prompt 写进额外参数）也不会破坏请求契约。
        payload: dict = dict(self._extra_params)
        payload.update(
            {
                "model": self._model,
                "prompt": prompt,
                self._size_param: self._size,
            }
        )
        headers = {"Authorization": f"Bearer {self._api_key}"}

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            async with _IMAGE_GATE:
                await _wait_for_interval(self._min_interval)
                attempt = 0
                while True:
                    attempt += 1
                    try:
                        return await self._generate_once(client, payload, headers)
                    except _TransientImageError as exc:
                        if attempt >= self._retry_attempts:
                            raise LLMError(
                                f"文生图被限流/服务端异常，已重试 {attempt} 次仍失败：{exc.message}。"
                                "如果反复出现，可以把模型换成付费的（如 cogview-4），"
                                "或者降低批量生成的张数",
                                code="image_rate_limited",
                            ) from exc
                        delay = exc.retry_after or self._retry_backoff * (2 ** (attempt - 1))
                        logger.warning(
                            "文生图被限流（第 %s/%s 次），%.1f 秒后重试：%s",
                            attempt,
                            self._retry_attempts,
                            delay,
                            exc.message,
                        )
                        await asyncio.sleep(delay)
                        _mark_called()

    async def _generate_once(self, client: httpx.AsyncClient, payload: dict, headers: dict) -> bytes:
        """真正发一次请求。429/5xx 抛 _TransientImageError 让上层重试，其它错误直接抛。"""
        try:
            resp = await client.post(
                f"{self._base_url}/images/generations", json=payload, headers=headers
            )
            _mark_called()
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            body = exc.response.text[:200]
            # 429 限流、5xx 服务端抽风 —— 这两类是"等一会儿可能就好"的
            if status == 429 or status >= 500:
                retry_after = _parse_retry_after(exc.response.headers.get("Retry-After"))
                raise _TransientImageError(f"HTTP {status}: {body}", retry_after) from exc
            raise LLMError(
                f"文生图服务返回错误状态 {status}: {body}",
                code="image_generation_failed",
            ) from exc
        except httpx.HTTPError as exc:
            raise _TransientImageError(f"网络错误: {exc}") from exc

        data = resp.json()
        ref = _extract_image_ref(data)

        # 情况一：返回 base64 图片数据
        if not ref.startswith("http"):
            try:
                return base64.b64decode(ref)
            except Exception as exc:  # noqa: BLE001
                raise AppError(
                    "文生图返回的内容既不是 URL 也不是合法 base64",
                    code="image_bad_output",
                    status_code=502,
                ) from exc

        # 情况二：返回临时 URL —— 立刻下载下来，避免过期
        try:
            file_resp = await client.get(ref)
            file_resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise LLMError(f"图片已生成但下载失败: {exc}", code="image_download_failed") from exc

        logger.info("文生图完成，图片大小 %s 字节", len(file_resp.content))
        return file_resp.content
