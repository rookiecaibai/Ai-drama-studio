"""真实的配音适配器（OpenAI 兼容的 /audio/speech）。

接口形态（OpenAI、硅基流动 CosyVoice、以及多数兼容实现都一样）：
    POST {base_url}/audio/speech
    { "model": ..., "input": "要读的文字", "voice": "音色", "response_format": "mp3" }
    -> 直接返回**二进制音频**（不是 JSON）

差异点：
- 少数实现返回 JSON（里面是音频 URL 或 base64），所以这里做了兼容解析；
- 部分厂商支持 instructions 之类的"语气"参数，不支持的发了会 400，
  所以是否附带镜头的 audio_prompt 由配置决定（TTS_INSTRUCTIONS_PARAM）。
"""

import base64
import json
import logging

import httpx

from app.core.errors import AppError, LLMError
from app.services.tts.base import TtsProvider

logger = logging.getLogger(__name__)


class OpenAICompatTtsProvider(TtsProvider):
    name = "openai_compat"

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model: str,
        voice: str,
        response_format: str = "mp3",
        instructions_param: str = "",
        timeout: float = 120.0,
    ):
        if not api_key:
            raise LLMError(
                "未配置 TTS_API_KEY，请把 TTS_PROVIDER 设为 mock 或填入真实 Key",
                code="tts_not_configured",
                status_code=500,
            )
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._voice = voice
        self._response_format = response_format
        self._instructions_param = instructions_param
        self._timeout = timeout
        self.extension = response_format if response_format in ("mp3", "wav", "opus", "aac", "flac") else "mp3"

    async def synthesize(
        self,
        *,
        text: str,
        style: str = "",
        voice: str | None = None,
        speed: float | None = None,
    ) -> bytes:
        payload: dict = {
            "model": self._model,
            "input": text,
            # 逐句音色优先；没指定就用配置里的默认音色
            "voice": voice or self._voice,
            "response_format": self._response_format,
        }
        if speed is not None:
            payload["speed"] = round(float(speed), 3)
        if self._instructions_param and style.strip():
            payload[self._instructions_param] = style.strip()

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            try:
                resp = await client.post(
                    f"{self._base_url}/audio/speech",
                    json=payload,
                    headers={"Authorization": f"Bearer {self._api_key}"},
                )
                resp.raise_for_status()
            except httpx.HTTPStatusError as exc:
                raise LLMError(
                    f"配音服务返回错误状态 {exc.response.status_code}: {exc.response.text[:200]}",
                    code="tts_generation_failed",
                ) from exc
            except httpx.HTTPError as exc:
                raise LLMError(f"调用配音服务失败: {exc}", code="tts_generation_failed") from exc

            content_type = resp.headers.get("content-type", "")

            # 情况一（标准）：直接返回音频字节
            if content_type.startswith("audio/") or content_type == "application/octet-stream":
                if not resp.content:
                    raise AppError("配音服务返回了空音频", code="tts_bad_output", status_code=502)
                return resp.content

            # 情况二：返回 JSON（里面装着 URL 或 base64）
            try:
                data = json.loads(resp.text)
            except json.JSONDecodeError as exc:
                raise AppError(
                    f"配音返回既不是音频也不是 JSON: {resp.text[:200]}",
                    code="tts_bad_output",
                    status_code=502,
                ) from exc

            url = data.get("url") or (data.get("data") or {}).get("url")
            b64 = data.get("b64_json") or data.get("audio") or (data.get("data") or {}).get("b64_json")

            if b64 and not url:
                return base64.b64decode(b64)
            if not url:
                raise AppError(f"配音返回里找不到音频: {str(data)[:200]}", code="tts_bad_output", status_code=502)

            try:
                file_resp = await client.get(url)
                file_resp.raise_for_status()
            except httpx.HTTPError as exc:
                raise LLMError(f"配音已生成但下载失败: {exc}", code="tts_download_failed") from exc

        logger.info("配音完成，音频大小 %s 字节", len(file_resp.content))
        return file_resp.content
