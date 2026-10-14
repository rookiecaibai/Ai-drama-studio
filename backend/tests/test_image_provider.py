"""真实文生图适配器的"契约测试"。

思路：**用一个本地假厂商服务代替真实厂商**。
这样能固定住我们这一侧的契约 —— 请求发到哪个路径、带了什么头、body 里有哪些字段、
厂商返回的几种结构能不能解析、报错会不会被正确映射。

好处非常实际：
- 真实厂商通常要花钱、要联网、还可能限流，不适合放进 CI；
- 但只要假服务的行为和厂商文档一致，适配器的正确性就能被持续验证；
- 换厂商时，只要改假服务模拟"新厂商的差异"，就能立刻知道适配器还缺什么。

剩下唯一的未知量就只有"真厂商的实际行为" —— 那需要一次人工验证。
"""

import asyncio
import base64
import http.server
import json
import socketserver
import threading
from io import BytesIO

import pytest
from PIL import Image

from app.core.errors import AppError, LLMError
from app.services.images.openai_compat import OpenAICompatImageProvider


def _make_png() -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (48, 48), (120, 60, 200)).save(buffer, format="PNG")
    return buffer.getvalue()


PNG = _make_png()


class _Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):  # 静音访问日志
        pass

    def _send_json(self, code: int, payload: dict) -> None:
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length)
        server: "_FakeVendor" = self.server  # type: ignore[assignment]
        server.received.append(
            {
                "path": self.path,
                "auth": self.headers.get("Authorization"),
                "payload": json.loads(raw or b"{}"),
            }
        )
        port = server.server_address[1]

        if server.mode == "url":
            self._send_json(200, {"data": [{"url": f"http://127.0.0.1:{port}/files/out.png"}]})
        elif server.mode == "b64":
            self._send_json(200, {"data": [{"b64_json": base64.b64encode(PNG).decode()}]})
        elif server.mode == "images_key":
            self._send_json(200, {"images": [{"url": f"http://127.0.0.1:{port}/files/out.png"}]})
        elif server.mode == "error":
            self._send_json(429, {"error": {"message": "rate limit exceeded"}})
        elif server.mode == "garbage":
            self._send_json(200, {"unexpected": "structure"})

    def do_GET(self) -> None:
        if self.path == "/files/out.png":
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Content-Length", str(len(PNG)))
            self.end_headers()
            self.wfile.write(PNG)
        else:
            self.send_response(404)
            self.end_headers()


class _FakeVendor(socketserver.ThreadingTCPServer):
    allow_reuse_address = True

    def __init__(self, mode: str = "url"):
        super().__init__(("127.0.0.1", 0), _Handler)
        self.mode = mode
        self.received: list[dict] = []


@pytest.fixture()
def vendor():
    server = _FakeVendor("url")
    threading.Thread(target=server.serve_forever, daemon=True).start()
    server.base_url = f"http://127.0.0.1:{server.server_address[1]}"  # type: ignore[attr-defined]
    try:
        yield server
    finally:
        server.shutdown()


def _provider(vendor, *, size_param: str = "size") -> OpenAICompatImageProvider:
    return OpenAICompatImageProvider(
        api_key="fake-key-123",
        base_url=vendor.base_url,  # type: ignore[attr-defined]
        model="cogview-3-flash",
        size="1024x1024",
        size_param=size_param,
    )


def _generate(vendor, *, size_param: str = "size") -> bytes:
    return asyncio.run(_provider(vendor, size_param=size_param).generate(prompt="a rainy night convenience store"))


def test_request_shape_and_download(vendor):
    """最常见的返回结构：临时 URL。顺带把请求契约全部断言一遍。"""
    data = _generate(vendor)

    assert data == PNG, "应该把临时 URL 上的图片下载下来"
    sent = vendor.received[0]
    assert sent["path"] == "/images/generations"
    assert sent["auth"] == "Bearer fake-key-123"
    assert sent["payload"]["model"] == "cogview-3-flash"
    assert sent["payload"]["prompt"].startswith("a rainy night")
    assert sent["payload"]["size"] == "1024x1024"
    # 只发 3 个字段：多余字段会被严格的厂商拒绝（比如 OpenAI 会因未知参数 400）
    assert sorted(sent["payload"]) == ["model", "prompt", "size"]


def test_base64_response_is_supported(vendor):
    vendor.mode = "b64"
    assert _generate(vendor) == PNG


def test_images_key_response_is_supported(vendor):
    """少数厂商用 images[0].url 而不是 data[0].url。"""
    vendor.mode = "images_key"
    assert _generate(vendor) == PNG


def test_size_param_name_is_configurable(vendor):
    """硅基流动用 image_size、OpenAI/智谱用 size —— 换厂商不该改代码。"""
    _generate(vendor, size_param="image_size")
    assert sorted(vendor.received[0]["payload"]) == ["image_size", "model", "prompt"]
    assert vendor.received[0]["payload"]["image_size"] == "1024x1024"


def test_vendor_error_is_mapped(vendor):
    vendor.mode = "error"
    with pytest.raises(LLMError) as exc:
        _generate(vendor)
    assert "429" in str(exc.value)


def test_unknown_response_shape_reports_clearly(vendor):
    vendor.mode = "garbage"
    with pytest.raises(AppError) as exc:
        _generate(vendor)
    assert "无法识别" in str(exc.value)


def test_missing_api_key_fails_fast():
    """没填 Key 就该在构造时立刻报错，而不是发一个注定 401 的请求出去。"""
    with pytest.raises(LLMError) as exc:
        OpenAICompatImageProvider(api_key="", base_url="http://x", model="m", size="1x1")
    assert "IMAGE_API_KEY" in str(exc.value)


def test_extra_params_are_merged_into_request(vendor):
    """厂商私有参数要能透传进请求体。

    典型用途：智谱去水印的 `watermark_enabled: false`。
    注意这个参数**必须配合控制台的"去水印管理"开关**才生效（官方文档写明）。
    """
    provider = OpenAICompatImageProvider(
        api_key="k",
        base_url=vendor.base_url,  # type: ignore[attr-defined]
        model="m",
        size="768x1344",
        extra_params={"watermark_enabled": False},
    )
    asyncio.run(provider.generate(prompt="x"))

    sent = vendor.received[0]["payload"]
    assert sent["watermark_enabled"] is False
    assert sent["model"] == "m"
    assert sent["prompt"] == "x"
    assert sent["size"] == "768x1344"


def test_required_fields_cannot_be_overridden_by_extra_params(vendor):
    """额外参数优先级必须低于必需字段 —— 否则配置写错就把请求搞坏了。"""
    provider = OpenAICompatImageProvider(
        api_key="k",
        base_url=vendor.base_url,  # type: ignore[attr-defined]
        model="m",
        size="768x1344",
        extra_params={"model": "hacked", "prompt": "hacked", "size": "1x1"},
    )
    asyncio.run(provider.generate(prompt="real"))

    sent = vendor.received[0]["payload"]
    assert sent["model"] == "m"
    assert sent["prompt"] == "real"
    assert sent["size"] == "768x1344"
