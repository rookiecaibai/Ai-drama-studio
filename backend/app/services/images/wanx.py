"""阿里云百炼 · 万相（wan2.7-image）适配器 —— **支持参考图**的图像生成。

**为什么值得为它单独写一个适配器**：
智谱的 cogview-3-flash / cogview-4 / glm-image 都是**纯文本输入**，
所以"同一个角色在不同镜头里长得像"只能靠文字描述（look_prompt），长相仍然会飘。
万相支持**多图输入**：把角色的单人人像当参考图传进去，模型会保持发型/脸型/服装。
这是本项目在"角色一致性"上第一次真正有可用的抓手。

**接口形状和 OpenAI 不兼容**（所以不能直接复用 openai_compat）：

    POST {base_url}/services/aigc/multimodal-generation/generation
    {
      "model": "wan2.7-image",
      "input": {"messages": [{"role": "user", "content": [
          {"image": "data:image/png;base64,..."},   // 参考图，可 0~9 张
          {"text":  "提示词"}
      ]}]},
      "parameters": {"size": "1K" | "768x1344", "n": 1, "watermark": false}
    }
    -> {"output": {"choices": [{"message": {"content": [{"image": "<url>", "type": "image"}]}}]}}

实测要点（都是真跑出来的）：
1. 参考图**必须传单个人物**。整张三视图（三个视角并排）会被理解成"三个人"，
   模型会画出一排三个一模一样的人 —— 所以调用前先经 `portrait.build_portrait` 裁剪。
2. 有参考图时，输出宽高比会**跟随参考图**（传 1K 也会变成竖长条），
   所以要显式传像素尺寸，才能和成片的 768x1344 对齐。
3. 返回的是 24 小时有效的临时 URL，必须立刻下载（和智谱的规则一样）。
"""

import base64
import logging
import re
from pathlib import Path

import httpx

from app.core.errors import AppError, LLMError
from app.services import media
from app.services.images.base import ImageProvider

logger = logging.getLogger(__name__)

#: 万相允许的图片格式
_MIME_BY_EXT = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png", "webp": "image/webp"}

#: 尺寸写法：其他地方都用 `768x1344`，但**万相要求 `768*1344`（星号）**。
#: 实测报错：`Invalid size format: 768x1344, expected format: width*height`
_SIZE_PATTERN = re.compile(r"^(\d+)\s*[xX×]\s*(\d+)$")


def _normalize_size(size: str) -> str:
    """把 `768x1344` 转成万相要求的 `768*1344`；`2K` 这类档位原样返回。"""
    raw = (size or "").strip()
    match = _SIZE_PATTERN.match(raw)
    if match:
        return f"{match.group(1)}*{match.group(2)}"
    return raw or "1K"


def _to_data_url(image_path: Path) -> str:
    """本地图片 -> data URL。按**文件头**判断格式（扩展名不可信，这个坑踩过）。"""
    data = image_path.read_bytes()
    mime = _MIME_BY_EXT.get(media.detect_image_extension(data), "image/png")
    return f"data:{mime};base64,{base64.b64encode(data).decode()}"


def _extract_image_url(payload: dict) -> str:
    """从返回里取图片地址，兼容新版 choices 和旧版 results 两种结构。"""
    output = payload.get("output") or {}
    for choice in output.get("choices") or []:
        message = choice.get("message") or {}
        for item in message.get("content") or []:
            if isinstance(item, dict) and isinstance(item.get("image"), str) and item["image"]:
                return item["image"]
    for item in output.get("results") or []:
        if isinstance(item, dict) and isinstance(item.get("url"), str) and item["url"]:
            return item["url"]
    raise AppError(
        f"万相返回的结构无法识别: {str(payload)[:300]}", code="image_bad_output", status_code=502
    )


class WanxImageProvider(ImageProvider):
    """万相图像生成/编辑（支持参考图）。"""

    name = "wanx"
    #: **这是关键**：告诉业务层"我能吃参考图"，于是 image_service 会把角色人像传进来
    supports_reference = True

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = "https://dashscope.aliyuncs.com/api/v1",
        model: str = "wan2.7-image",
        size: str = "1K",
        timeout: float = 300.0,
    ):
        if not api_key:
            raise LLMError(
                "未配置万相的 API Key（WANX_API_KEY 或 IMAGE_API_KEY）",
                code="image_not_configured",
                status_code=500,
            )
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._size = size
        self._timeout = timeout

    async def generate(self, *, prompt: str, reference_image: Path | None = None) -> bytes:
        # 参考图在前、提示词在后 —— 和官方示例的顺序一致（多图时顺序有含义）
        content: list[dict] = []
        if reference_image is not None:
            content.append({"image": _to_data_url(reference_image)})
        content.append({"text": prompt})

        payload = {
            "model": self._model,
            "input": {"messages": [{"role": "user", "content": content}]},
            "parameters": {"size": _normalize_size(self._size), "n": 1, "watermark": False},
        }
        headers = {"Authorization": f"Bearer {self._api_key}", "Content-Type": "application/json"}

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            try:
                resp = await client.post(
                    f"{self._base_url}/services/aigc/multimodal-generation/generation",
                    json=payload,
                    headers=headers,
                )
            except httpx.HTTPError as exc:
                raise LLMError(f"调用万相失败（{type(exc).__name__}）: {exc}", code="image_generation_failed") from exc

            if resp.status_code != 200:
                raise LLMError(
                    f"万相返回 {resp.status_code}: {resp.text[:300]}",
                    code="image_generation_failed",
                )

            url = _extract_image_url(resp.json())
            logger.info(
                "万相出图完成（%s参考图）", "带" if reference_image is not None else "无"
            )

            # 结果 URL 24 小时失效 —— 立刻下载，URL 用完即弃
            try:
                file_resp = await client.get(url)
                file_resp.raise_for_status()
            except httpx.HTTPError as exc:
                raise LLMError(f"图片已生成但下载失败: {exc}", code="image_download_failed") from exc

        return file_resp.content
