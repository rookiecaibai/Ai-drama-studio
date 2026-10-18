"""文生图"限流重试"的测试。

**这组测试来自一次真实事故**：用户同时点了「生成三视图」和「生成画面」，
两个后台任务并发打同一个账户，智谱返回 429（账户级速率限制），
4 张图全部失败 —— 而 429 的语义本来是"稍后再试"。

所以这里守护三件事：
    ① 429 会**重试**，而且重试后能成功；
    ② 重试次数用完才报错，且错误信息说人话（包含"限流"）；
    ③ 重试**不吞**真正的业务错误（400/401 这类重试也没用的，立刻失败）。
"""

import asyncio
import base64
import http.server
import json as jsonlib
import socketserver
import threading
import time

import pytest

from app.core.errors import LLMError
from app.services.images.openai_compat import OpenAICompatImageProvider

#: 一个最小的合法 PNG（只要能被 base64 解出来即可，测试不校验图片内容）
FAKE_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


class FakeVendor:
    """可控的假厂商：前 N 次返回指定状态码，之后返回成功。"""

    def __init__(self, *, fail_times: int, status: int = 429):
        self.calls = 0
        self.fail_times = fail_times
        self.status = status

    def build(self):
        vendor = self

        class Handler(http.server.BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args):
                pass

            def _send(self, code, payload):
                body = jsonlib.dumps(payload).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self):
                length = int(self.headers.get("Content-Length", 0))
                self.rfile.read(length)
                vendor.calls += 1
                if vendor.calls <= vendor.fail_times:
                    # 注意是 self._send：写 vendor._send 会让 handler 抛 AttributeError，
                    # 客户端只能看到"连接被断开"，测试就测不出真正想验证的东西
                    self._send(
                        vendor.status,
                        {"error": {"code": "1302", "message": "您的账户已达到速率限制"}},
                    )
                    return
                self._send(200, {"data": [{"b64_json": base64.b64encode(FAKE_PNG).decode()}]})

        server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), Handler)
        server.daemon_threads = True
        threading.Thread(target=server.serve_forever, daemon=True).start()
        return server


def _provider(server, **kwargs):
    """默认把节流和退避压到最小，否则每个用例白等好几秒。"""
    options = {"min_interval": 0.0, "retry_backoff": 0.05, "retry_attempts": 3}
    options.update(kwargs)
    return OpenAICompatImageProvider(
        api_key="fake-key",
        base_url=f"http://127.0.0.1:{server.server_address[1]}",
        model="cogview-3-flash",
        size="1024x1024",
        timeout=10,
        **options,
    )


def test_rate_limit_is_retried_and_eventually_succeeds():
    """429 两次、第三次成功 —— 最终必须拿到图，而不是把整批任务判死。"""
    vendor = FakeVendor(fail_times=2)
    server = vendor.build()
    try:
        data = asyncio.run(_provider(server, retry_attempts=3).generate(prompt="一只猫"))
    finally:
        server.shutdown()

    assert data == FAKE_PNG, "重试之后应该真的拿到图"
    assert vendor.calls == 3, f"应该尝试 3 次（2 次 429 + 1 次成功），实际 {vendor.calls}"


def test_gives_up_after_max_attempts_with_a_readable_message():
    """重试用完就该失败，并且错误信息要让人看懂"是被限流了"。"""
    vendor = FakeVendor(fail_times=99)
    server = vendor.build()
    try:
        with pytest.raises(LLMError) as exc:
            asyncio.run(_provider(server, retry_attempts=2).generate(prompt="一只猫"))
    finally:
        server.shutdown()

    message = str(exc.value)
    assert "重试" in message and "限流" in message, message
    assert "cogview-4" in message, "应该给出可操作的下一步建议（换付费模型）"
    assert vendor.calls == 2


def test_business_errors_are_not_retried():
    """401/400 这类错误重试一万次也没用，必须立刻失败 —— 否则只是白白拖慢。"""
    vendor = FakeVendor(fail_times=99, status=401)
    server = vendor.build()
    try:
        with pytest.raises(LLMError) as exc:
            asyncio.run(_provider(server, retry_attempts=3).generate(prompt="一只猫"))
    finally:
        server.shutdown()

    assert vendor.calls == 1, "401 不该重试"
    assert "401" in str(exc.value)


def test_requests_are_throttled_by_min_interval():
    """两次请求之间必须有最小间隔 —— 这是"别自己把限流打满"的第一道防线。"""
    vendor = FakeVendor(fail_times=0)
    server = vendor.build()
    try:
        provider = _provider(server, min_interval=0.3)

        async def two_calls():
            started = time.monotonic()
            await provider.generate(prompt="图一")
            await provider.generate(prompt="图二")
            return time.monotonic() - started

        elapsed = asyncio.run(two_calls())
    finally:
        server.shutdown()

    assert vendor.calls == 2
    assert elapsed >= 0.3, f"两次请求之间应该至少隔 0.3 秒，实际总耗时 {elapsed:.2f}s"
