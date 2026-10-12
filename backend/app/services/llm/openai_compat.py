"""OpenAI 兼容协议的 Provider。

为什么用"兼容协议"而不是绑死某一家：
DeepSeek、通义千问、Kimi、智谱、OpenAI、本地 vLLM/Ollama 都提供
`POST {base_url}/chat/completions` 这套接口。写一份适配器，改 base_url + model
就能切模型，这对小团队快速试错非常重要。
"""

import httpx

from app.core.errors import LLMError
from app.services.llm.base import LLMProvider


class OpenAICompatProvider(LLMProvider):
    name = "openai_compat"

    def __init__(self, *, api_key: str, base_url: str, model: str, timeout: float = 60.0):
        if not api_key:
            raise LLMError("未配置 LLM_API_KEY，请改用 mock 或填入真实 Key", code="llm_not_configured", status_code=500)
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._timeout = timeout

    async def complete(self, *, system: str, user: str) -> str:
        payload = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0.7,
            # 明确要求 JSON 输出，能显著降低解析失败率（下面 script_service 仍会做兜底解析）
            "response_format": {"type": "json_object"},
        }

        # 每-次调用新建 client 是为了简单；高并发场景应复用全局 AsyncClient（见 docs/00 待优化项）
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            try:
                resp = await client.post(
                    f"{self._base_url}/chat/completions",
                    json=payload,
                    headers={"Authorization": f"Bearer {self._api_key}"},
                )
                resp.raise_for_status()
            except httpx.HTTPStatusError as exc:
                raise LLMError(f"大模型返回错误状态 {exc.response.status_code}: {exc.response.text[:200]}") from exc
            except httpx.HTTPError as exc:
                raise LLMError(f"调用大模型失败: {exc}") from exc

        data = resp.json()
        try:
            return data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError(f"大模型返回结构不符合预期: {str(data)[:200]}") from exc
