"""真实的图生视频适配器（智谱 CogVideoX 系列，异步接口）。

接口协议（来自官方 OpenAPI 文档，不是猜的）：

    提交: POST {base_url}/videos/generations
          { "model": "cogvideox-3", "image_url": "data:image/jpeg;base64,...",
            "prompt": "让画面动起来", "size": "720x1280", "duration": 5,
            "quality": "speed", "watermark_enabled": false }
          -> { "id": "...", "task_status": "PROCESSING" }

    轮询: GET  {base_url}/async-result/{id}
          -> { "task_status": "PROCESSING" | "SUCCESS" | "FAIL",
               "video_result": [ { "url": "...", "cover_image_url": "..." } ] }

**为什么要提交 + 轮询两层循环**
视频生成很慢（几十秒到几分钟），厂商一律做成异步任务。
这也正是我们自己那套"任务表 + 轮询"模式的同款设计 ——
区别只是我们要在**后台任务内部**再轮询一次厂商。

**图片用 base64 而不是 URL**
我们的图片存在自己磁盘上、走带鉴权的接口，没有公网地址。
官方文档写明 image_url 支持 base64（`data:image/jpeg;base64,...`），
所以本地图片可以直接用 —— 这是能在本地跑通这条链路的关键。
"""

import asyncio
import base64
import logging
from pathlib import Path

import httpx

from app.core.errors import AppError, LLMError
from app.services import media
from app.services.videos.base import VideoClipProvider

logger = logging.getLogger(__name__)

#: 官方限制：图片不超过 5MB、支持 png/jpeg/jpg
MAX_IMAGE_BYTES = 5 * 1024 * 1024

_MIME_BY_EXT = {"jpg": "image/jpeg", "png": "image/png", "webp": "image/webp", "jpeg": "image/jpeg"}

#: 终态之外的状态都算"还在处理"
_PROCESSING = {"PROCESSING", "RUNNING", "QUEUED", "PENDING"}


def to_data_url(image_path: Path) -> str:
    """把本地图片变成 `data:image/jpeg;base64,...`。

    格式按**文件头**判断，不按扩展名 —— 厂商返回的图片格式和扩展名不一定一致
    （我们就被智谱的 JPEG 坑过一次，见 media.detect_image_extension）。
    """
    data = image_path.read_bytes()
    if len(data) > MAX_IMAGE_BYTES:
        raise AppError(
            f"图片超过厂商限制（{len(data) // 1024}KB > {MAX_IMAGE_BYTES // 1024}KB）",
            code="image_too_large",
            status_code=400,
        )
    mime = _MIME_BY_EXT.get(media.detect_image_extension(data), "image/jpeg")
    return f"data:{mime};base64,{base64.b64encode(data).decode()}"


def _extract_video_url(payload: dict) -> str:
    """从异步结果里取出视频地址（兼容几种字段名）。"""
    candidates: list[object] = []
    for key in ("video_result", "videos", "data"):
        items = payload.get(key)
        if isinstance(items, list) and items:
            first = items[0]
            if isinstance(first, dict):
                candidates += [first.get("url"), first.get("video_url")]
            elif isinstance(first, str):
                candidates.append(first)
    candidates += [payload.get("url"), payload.get("video_url")]

    for item in candidates:
        if isinstance(item, str) and item.strip():
            return item.strip()
    raise AppError(
        f"视频生成成功但找不到视频地址: {str(payload)[:200]}",
        code="video_bad_output",
        status_code=502,
    )


class OpenAICompatVideoClipProvider(VideoClipProvider):
    name = "openai_compat"

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model: str,
        size: str,
        quality: str = "speed",
        seconds: int = 5,
        send_duration: bool = True,
        extra_params: dict | None = None,
        timeout: float = 60.0,
        poll_interval: float = 6.0,
        poll_timeout: float = 900.0,
    ):
        if not api_key:
            raise LLMError(
                "未配置 VIDEO_API_KEY（也没有可复用的 IMAGE_API_KEY），"
                "请把 VIDEO_CLIP_PROVIDER 设为 mock 或填入真实 Key",
                code="video_not_configured",
                status_code=500,
            )
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._size = size
        self._quality = quality
        self._seconds = seconds
        self._send_duration = send_duration
        self._extra_params = extra_params or {}
        self._timeout = timeout
        self._poll_interval = poll_interval
        self._poll_timeout = poll_timeout

    @property
    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self._api_key}"}

    async def generate(self, *, image_path: Path, prompt: str) -> bytes:
        data_url = to_data_url(image_path)

        # 先铺厂商私有参数，必需字段随后覆盖（和文生图、配音一致的优先级规则）
        payload: dict = dict(self._extra_params)
        payload.update(
            {
                "model": self._model,
                "image_url": data_url,
                # 官方限制 prompt 不超过 512 字符
                "prompt": (prompt or "让画面动起来")[:512],
                "size": self._size,
                "quality": self._quality,
            }
        )
        # duration 只有 cogvideox-3 支持；cogvideox-2 / cogvideox-flash 没有这个参数。
        # 做成开关而不是按模型名硬判断：厂商的枚举随时会变，
        # 由使用者按当前文档决定更可靠（配错了会拿到一条明确的 400，而不是猜）。
        if self._send_duration:
            payload["duration"] = self._seconds

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            task_id = await self._submit(client, payload)
            video_url = await self._wait_for_result(client, task_id)
            return await self._download(client, video_url)

    async def _submit(self, client: httpx.AsyncClient, payload: dict) -> str:
        try:
            resp = await client.post(
                f"{self._base_url}/videos/generations", json=payload, headers=self._headers
            )
        except httpx.HTTPError as exc:
            # 把异常类型也写出来：网络层的异常种类很多（超时/连接中断/协议错误），
            # 只写一句 "失败" 等于没说，排查时完全无从下手。
            raise LLMError(
                f"提交图生视频任务失败（{type(exc).__name__}）: {exc}",
                code="video_submit_failed",
            ) from exc

        if resp.status_code == 429:
            raise LLMError(
                f"图生视频模型当前排队/限流（HTTP 429）：{resp.text[:160]}",
                code="video_rate_limited",
            )
        if resp.status_code != 200:
            raise LLMError(
                f"提交图生视频任务返回 {resp.status_code}: {resp.text[:200]}",
                code="video_submit_failed",
            )

        data = resp.json()
        task_id = data.get("id")
        if not task_id:
            raise AppError(
                f"提交成功但没拿到任务 id: {str(data)[:200]}", code="video_bad_output", status_code=502
            )
        logger.info("图生视频任务已提交：id=%s model=%s", task_id, self._model)
        return task_id

    async def _wait_for_result(self, client: httpx.AsyncClient, task_id: str) -> str:
        waited = 0.0
        while waited < self._poll_timeout:
            await asyncio.sleep(self._poll_interval)
            waited += self._poll_interval

            try:
                resp = await client.get(
                    f"{self._base_url}/async-result/{task_id}", headers=self._headers
                )
                resp.raise_for_status()
            except httpx.HTTPStatusError as exc:
                status = exc.response.status_code
                if 400 <= status < 500:
                    # **4xx 是永久性错误，必须立刻失败** —— 实测踩过这个坑：
                    # 任务 id 失效时接口返回 400，而"网络抖动就重试"的逻辑
                    # 会把一次 400 变成"傻等 900 秒才超时"，用户以为卡死了。
                    raise LLMError(
                        f"查询图生视频结果失败（HTTP {status}，任务 {task_id}）："
                        f"{exc.response.text[:200]}",
                        code="video_task_not_found",
                    ) from exc
                # 5xx 才可能是服务端临时故障，重试
                logger.warning("轮询图生视频结果失败（服务端 %s，继续重试）: %s", status, exc)
                continue
            except httpx.HTTPError as exc:
                # 网络层抖动（超时、连接中断）才是真的可以重试
                logger.warning("轮询图生视频结果失败（继续重试）: %s", exc)
                continue

            data = resp.json()
            status = str(data.get("task_status", "")).upper()
            if status in _PROCESSING:
                continue
            if status == "SUCCESS":
                return _extract_video_url(data)
            # FAIL 或其它未知终态
            raise LLMError(
                f"图生视频任务失败（task_status={status or '未知'}）: {str(data)[:200]}",
                code="video_generation_failed",
            )

        raise LLMError(
            f"图生视频超时（等了 {int(self._poll_timeout)} 秒仍未完成）",
            code="video_timeout",
        )

    async def _download(self, client: httpx.AsyncClient, video_url: str) -> bytes:
        try:
            resp = await client.get(video_url, timeout=max(self._timeout, 120.0))
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise LLMError(f"视频已生成但下载失败: {exc}", code="video_download_failed") from exc

        if not resp.content:
            raise AppError("下载到的视频是空的", code="video_bad_output", status_code=502)
        logger.info("图生视频完成，大小 %.0f KB", len(resp.content) / 1024)
        return resp.content
