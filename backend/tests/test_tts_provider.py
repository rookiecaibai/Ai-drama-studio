"""真实配音适配器的契约测试（同样用本地假厂商服务）。

固定住我们这一侧的契约：
- 请求打到 /audio/speech，带上 Bearer 鉴权，body 里有哪些字段
- 标准返回（二进制音频）、JSON+URL、JSON+base64 三种都要能解析
- 厂商报错要映射成统一错误
- instructions 参数默认不发（不支持的厂商会 400），配置了才发
"""

import asyncio
import base64
import http.server
import json
import socketserver
import threading

import pytest

from app.core.errors import LLMError
from app.services.tts.openai_compat import OpenAICompatTtsProvider

AUDIO = b"ID3\x03\x00\x00\x00" + b"\x11" * 512  # 一段假的 mp3 字节


class _Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):
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

        if server.mode == "binary":
            self.send_response(200)
            self.send_header("Content-Type", "audio/mpeg")
            self.send_header("Content-Length", str(len(AUDIO)))
            self.end_headers()
            self.wfile.write(AUDIO)
        elif server.mode == "json_url":
            self._send_json(200, {"url": f"http://127.0.0.1:{port}/files/out.mp3"})
        elif server.mode == "json_b64":
            self._send_json(200, {"data": {"b64_json": base64.b64encode(AUDIO).decode()}})
        elif server.mode == "error":
            self._send_json(400, {"error": {"message": "unsupported voice"}})

    def do_GET(self) -> None:
        if self.path == "/files/out.mp3":
            self.send_response(200)
            self.send_header("Content-Type", "audio/mpeg")
            self.send_header("Content-Length", str(len(AUDIO)))
            self.end_headers()
            self.wfile.write(AUDIO)
        else:
            self.send_response(404)
            self.end_headers()


class _FakeVendor(socketserver.ThreadingTCPServer):
    allow_reuse_address = True

    def __init__(self, mode: str = "binary"):
        super().__init__(("127.0.0.1", 0), _Handler)
        self.mode = mode
        self.received: list[dict] = []


@pytest.fixture()
def vendor():
    server = _FakeVendor("binary")
    threading.Thread(target=server.serve_forever, daemon=True).start()
    server.base_url = f"http://127.0.0.1:{server.server_address[1]}"  # type: ignore[attr-defined]
    try:
        yield server
    finally:
        server.shutdown()


def _provider(vendor, *, instructions_param: str = "") -> OpenAICompatTtsProvider:
    return OpenAICompatTtsProvider(
        api_key="fake-tts-key",
        base_url=vendor.base_url,  # type: ignore[attr-defined]
        model="FunAudioLLM/CosyVoice2-0.5B",
        voice="claire",
        response_format="mp3",
        instructions_param=instructions_param,
    )


def _speak(vendor, *, text="你好，这里是深夜便利店。", style="", instructions_param=""):
    return asyncio.run(
        _provider(vendor, instructions_param=instructions_param).synthesize(text=text, style=style)
    )


def test_binary_audio_response(vendor):
    data = _speak(vendor)
    assert data == AUDIO

    sent = vendor.received[0]
    assert sent["path"] == "/audio/speech"
    assert sent["auth"] == "Bearer fake-tts-key"
    assert sent["payload"]["model"] == "FunAudioLLM/CosyVoice2-0.5B"
    assert sent["payload"]["input"].startswith("你好")
    assert sent["payload"]["voice"] == "claire"
    assert sent["payload"]["response_format"] == "mp3"


def test_instructions_not_sent_by_default(vendor):
    """不支持的厂商收到未知字段会 400，所以默认不能发。"""
    _speak(vendor, style="低声、急促")
    assert "instructions" not in vendor.received[0]["payload"]
    assert sorted(vendor.received[0]["payload"]) == ["input", "model", "response_format", "voice"]


def test_instructions_sent_when_configured(vendor):
    _speak(vendor, style="低声、急促", instructions_param="instructions")
    assert vendor.received[0]["payload"]["instructions"] == "低声、急促"


def test_json_url_response(vendor):
    vendor.mode = "json_url"
    assert _speak(vendor) == AUDIO


def test_json_base64_response(vendor):
    vendor.mode = "json_b64"
    assert _speak(vendor) == AUDIO


def test_vendor_error_is_mapped(vendor):
    vendor.mode = "error"
    with pytest.raises(LLMError) as exc:
        _speak(vendor)
    assert "400" in str(exc.value)


def test_missing_key_fails_fast():
    with pytest.raises(LLMError) as exc:
        OpenAICompatTtsProvider(api_key="", base_url="http://x", model="m", voice="v")
    assert "TTS_API_KEY" in str(exc.value)


def test_extra_params_and_speed_are_sent(vendor):
    """配音也要能透传厂商私有参数（比如去水印），并且支持逐句指定语速。"""
    provider = OpenAICompatTtsProvider(
        api_key="k",
        base_url=vendor.base_url,  # type: ignore[attr-defined]
        model="glm-tts",
        voice="tongtong",
        extra_params={"watermark_enabled": False},
    )
    asyncio.run(provider.synthesize(text="你好", voice="xiaochen", speed=1.25))

    sent = vendor.received[0]["payload"]
    assert sent["watermark_enabled"] is False
    assert sent["voice"] == "xiaochen", "逐句音色要覆盖默认音色"
    assert sent["speed"] == 1.25
    assert sent["model"] == "glm-tts"


def test_mock_provider_produces_valid_wav():
    """mock 也要产出**真的能播放**的 WAV —— 否则"音频能不能播"这件事就没被验证。"""
    from app.services.tts.mock import MockTtsProvider

    data = asyncio.run(MockTtsProvider().synthesize(text="这是一句测试台词", style="平静"))
    assert data[:4] == b"RIFF"
    assert data[8:12] == b"WAVE"
    assert len(data) > 10_000

    # 采样率与声道数应写在 fmt 块里（22 字节处开始，前 4 字节是 fmt 块长度）
    import struct

    channels, sample_rate = struct.unpack("<HI", data[22:28])
    assert channels == 1
    assert sample_rate == 22050


def test_mock_provider_scales_duration_with_text_length():
    import struct

    from app.services.tts.mock import MockTtsProvider

    provider = MockTtsProvider()

    def duration_of(text: str) -> float:
        data = asyncio.run(provider.synthesize(text=text))
        byte_rate = struct.unpack("<I", data[28:32])[0]
        data_size = struct.unpack("<I", data[40:44])[0]
        return data_size / byte_rate

    short = duration_of("短")
    long = duration_of("这是一句明显更长的台词，用来验证时长会随文字长度增长。" * 2)
    assert long > short
