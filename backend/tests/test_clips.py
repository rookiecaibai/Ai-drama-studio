"""图生视频（动态片段）的测试。

这组测试要回答用户最直接的那个质疑：
**"你说让画面动起来了，凭什么？"**
所以旗舰用例是像素级的：提取片段的首帧和末帧，逐像素比对，
用"平均绝对差 + 明显变化像素占比"量化到底动了多少 —— 不看文件哈希
（哈希会被 PNG 元数据干扰，我们就在这里被坑过一次）。
"""

import asyncio
import random
import subprocess
from pathlib import Path

import pytest
from PIL import Image, ImageChops, ImageStat

from app.api.deps import get_clip_provider
from app.services import media
from app.services.ffmpeg import ffmpeg_exe
from app.services.video_service import shot_source
from app.services.videos.kenburns import render_ken_burns
from app.services.videos.mock import MockVideoClipProvider

WIDTH, HEIGHT, FPS, SECONDS = 240, 426, 12, 1.5  # 测试用小尺寸，跑得快


def _write_test_image(path: Path) -> None:
    """造一张**固定种子的随机噪声图**当测试素材。

    这里连续踩了两个坑，都值得记下来：
    ① 一开始用棋盘格 —— 它是**周期图案**，平移正好接近一个周期时前后看起来一样，
       "平移运镜"几乎测不出来；
    ② 换成二维渐变 —— 又太**平滑**，缩放时大片区域的色差变化不到判定阈值（8/255），
       同样测不出"明显变化"。
    随机噪声同时避开了这两个问题：非周期 + 高频，任何位移/缩放都会让绝大多数像素剧变。
    （真实照片介于两者之间，实测缩放/平移都在 45%~65%，所以这只是测试素材的选材问题。）
    """
    rng = random.Random(20260101)  # 固定种子 -> 结果可复现
    image = Image.new("RGB", (WIDTH, HEIGHT))
    pixels = image.load()
    for y in range(HEIGHT):
        for x in range(WIDTH):
            pixels[x, y] = (rng.randrange(256), rng.randrange(256), rng.randrange(256))
    image.save(path)


def _extract_frame(video: Path, at: float, out: Path) -> None:
    subprocess.run(
        [ffmpeg_exe(), "-y", "-ss", str(at), "-i", str(video), "-frames:v", "1", str(out)],
        capture_output=True,
    )


def _motion_score(first: Path, last: Path) -> tuple[float, float]:
    """返回 (平均绝对差 0-255, 明显变化像素占比 %)。

    只看"变化超过 8"的像素占比：H.264 编码本身有微小噪声，
    不做阈值过滤的话"完全静止"的视频也会算出一丁点差异。
    """
    img_a = Image.open(first).convert("RGB")
    img_b = Image.open(last).convert("RGB")
    diff = ImageChops.difference(img_a, img_b)
    mad = sum(ImageStat.Stat(diff).mean) / 3
    histogram = diff.convert("L").histogram()
    total = sum(histogram)
    return mad, (sum(histogram[9:]) / total * 100 if total else 0.0)


def test_ken_burns_actually_moves_the_picture(tmp_path):
    """核心用例：运镜版首末帧必须有可见差异，静止版必须没有。"""
    source = tmp_path / "src.png"
    _write_test_image(source)

    animated = tmp_path / "animated.mp4"
    render_ken_burns(source, animated, seconds=SECONDS, width=WIDTH, height=HEIGHT, fps=FPS, variant=0)
    assert animated.stat().st_size > 0, "应该产出真实的视频文件"

    a, b = tmp_path / "a.png", tmp_path / "b.png"
    _extract_frame(animated, 0.05, a)
    _extract_frame(animated, SECONDS - 0.1, b)
    mad, ratio = _motion_score(a, b)
    assert mad > 5, f"运镜应该带来明显的像素差异，实际平均差只有 {mad:.2f}"
    assert ratio > 20, f"应该有大量像素发生变化，实际只有 {ratio:.1f}%"

    # 对照：改造前的静止定格，必须几乎没有任何变化
    static = tmp_path / "static.mp4"
    subprocess.run(
        [
            ffmpeg_exe(), "-y", "-loop", "1", "-i", str(source),
            "-vf", f"scale={WIDTH}:{HEIGHT},fps={FPS}",
            "-t", str(SECONDS), "-c:v", "libx264", "-pix_fmt", "yuv420p", str(static),
        ],
        capture_output=True,
    )
    _extract_frame(static, 0.05, a)
    _extract_frame(static, SECONDS - 0.1, b)
    _, static_ratio = _motion_score(a, b)
    assert static_ratio < 1.0, f"静止版不该有可见变化，实际 {static_ratio:.1f}%"


def test_all_four_camera_moves_work(tmp_path):
    """四种运镜（推近/拉远/左右平移）都必须真的动起来。"""
    source = tmp_path / "src.png"
    _write_test_image(source)

    for variant in range(4):
        video = tmp_path / f"v{variant}.mp4"
        render_ken_burns(
            source, video, seconds=SECONDS, width=WIDTH, height=HEIGHT, fps=FPS, variant=variant
        )
        a, b = tmp_path / f"v{variant}_a.png", tmp_path / f"v{variant}_b.png"
        _extract_frame(video, 0.05, a)
        _extract_frame(video, SECONDS - 0.1, b)
        _, ratio = _motion_score(a, b)
        assert ratio > 20, f"运镜模式 {variant} 没有明显变化（{ratio:.1f}%）"


def test_mock_provider_returns_real_video_bytes():
    provider = MockVideoClipProvider(width=WIDTH, height=HEIGHT, fps=FPS, seconds=SECONDS)
    source = Path("_clip_src.png")
    _write_test_image(source)
    try:
        data = asyncio.run(provider.generate(image_path=source, prompt="镜头缓慢推进"))
    finally:
        source.unlink(missing_ok=True)

    assert len(data) > 1000, "应该返回真实的视频字节"
    assert data[4:8] == b"ftyp", "应该是一个真正的 MP4"


# ───────────────────────── 素材选择：有片段就用片段 ─────────────────────────


def test_shot_source_prefers_clip_and_falls_back_to_image():
    """合成时优先用动态片段；片段缺失必须静默退回静态图（向后兼容）。"""
    from app.db.models import Shot

    image_rel = media.shot_image_rel_path(987654, "jpg")
    clip_rel = media.shot_clip_rel_path(987654)
    media.save_bytes(image_rel, b"fake-image")
    media.save_bytes(clip_rel, b"fake-clip")

    shot = Shot(number=1, image_path=image_rel, clip_path=clip_rel)
    path, is_video = shot_source(shot)
    assert is_video is True
    assert path == media.absolute_path(clip_rel)

    # 片段文件不在了（比如被清理）-> 退回静态图，而不是报错
    shot.clip_path = "clips/shots/not-exists.mp4"
    path, is_video = shot_source(shot)
    assert is_video is False
    assert path == media.absolute_path(image_rel)

    # 两个都没有 -> None（上层会给出"请先生成画面"的明确报错）
    shot.image_path = "shots/not-exists.jpg"
    assert shot_source(shot) == (None, False)


# ───────────────────────── 接口与任务 ─────────────────────────


def _prepare_with_images(client):
    drama = client.post("/api/v1/dramas", json={"title": "深夜便利店", "genre": "悬疑"}).json()
    episode = client.post(
        f"/api/v1/dramas/{drama['id']}/episodes",
        json={"title": "第1集", "synopsis": "小林在夜班时发现异常。"},
    ).json()
    client.post(f"/api/v1/episodes/{episode['id']}/generate", json={"shot_count": 2})
    task = client.post(f"/api/v1/episodes/{episode['id']}/images", json={}).json()
    assert client.get(f"/api/v1/tasks/{task['id']}").json()["status"] == "succeeded"
    return drama, episode


def _use_fast_mock_clips(client):
    """测试里用小尺寸运镜，别把测试跑成几分钟。"""
    client.app.dependency_overrides[get_clip_provider] = lambda: MockVideoClipProvider(
        width=WIDTH, height=HEIGHT, fps=FPS, seconds=SECONDS
    )


def test_clip_task_disabled_returns_400(client):
    """没启用图生视频时要给出明确提示，而不是静默什么都不做。"""
    _, episode = _prepare_with_images(client)
    resp = client.post(f"/api/v1/episodes/{episode['id']}/clips", json={})
    assert resp.status_code == 400
    assert "VIDEO_CLIP_PROVIDER" in resp.json()["error"]["message"]


def test_clip_task_generates_clips_for_every_shot(client):
    _use_fast_mock_clips(client)
    _, episode = _prepare_with_images(client)

    resp = client.post(f"/api/v1/episodes/{episode['id']}/clips", json={})
    assert resp.status_code == 202, resp.text
    task = resp.json()
    assert task["kind"] == "clips"

    finished = client.get(f"/api/v1/tasks/{task['id']}").json()
    assert finished["status"] == "succeeded", finished.get("error")
    assert finished["total"] > 0
    assert finished["done"] == finished["total"]

    detail = client.get(f"/api/v1/episodes/{episode['id']}").json()
    for shot in detail["shots"]:
        assert shot["clip_path"], f"镜头 {shot['number']} 应该有了动态片段"
        assert shot["clip_error"] is None

        preview = client.get(f"/api/v1/shots/{shot['id']}/clip")
        assert preview.status_code == 200
        assert preview.headers["content-type"] == "video/mp4"
        assert preview.content[4:8] == b"ftyp"


def test_clip_is_only_generated_once_by_default(client):
    _use_fast_mock_clips(client)
    _, episode = _prepare_with_images(client)

    first = client.post(f"/api/v1/episodes/{episode['id']}/clips", json={}).json()
    client.get(f"/api/v1/tasks/{first['id']}")

    second = client.post(f"/api/v1/episodes/{episode['id']}/clips", json={}).json()
    finished = client.get(f"/api/v1/tasks/{second['id']}").json()
    assert finished["total"] == 0, "默认只补缺，已经有的不该重做（视频很贵）"


def test_clip_requires_images_first(client):
    """还没有画面就想生成动态片段 -> 明确报错。"""
    _use_fast_mock_clips(client)
    drama = client.post("/api/v1/dramas", json={"title": "空剧", "genre": "都市"}).json()
    episode = client.post(f"/api/v1/dramas/{drama['id']}/episodes", json={"title": "第1集"}).json()
    client.post(f"/api/v1/episodes/{episode['id']}/generate", json={"shot_count": 2})

    task = client.post(f"/api/v1/episodes/{episode['id']}/clips", json={}).json()
    finished = client.get(f"/api/v1/tasks/{task['id']}").json()
    assert finished["status"] == "failed"
    assert "画面" in finished["error"]


def test_clip_preview_of_other_user_is_hidden(client, login_as):
    _use_fast_mock_clips(client)
    _, episode = _prepare_with_images(client)
    task = client.post(f"/api/v1/episodes/{episode['id']}/clips", json={}).json()
    client.get(f"/api/v1/tasks/{task['id']}")

    detail = client.get(f"/api/v1/episodes/{episode['id']}").json()
    shot_id = detail["shots"][0]["id"]

    token = login_as("bob")
    client.headers["Authorization"] = f"Bearer {token}"
    assert client.get(f"/api/v1/shots/{shot_id}/clip").status_code == 404
    assert client.post(f"/api/v1/episodes/{episode['id']}/clips", json={}).status_code == 404


def test_clip_preview_without_clip_is_404(client):
    _, episode = _prepare_with_images(client)
    detail = client.get(f"/api/v1/episodes/{episode['id']}").json()
    shot_id = detail["shots"][0]["id"]
    resp = client.get(f"/api/v1/shots/{shot_id}/clip")
    assert resp.status_code == 404
    assert "动态片段" in resp.json()["error"]["message"]


def test_clip_failure_is_isolated_per_shot(client):
    """某个镜头生成失败，其它镜头继续（视频模型最容易限流/超时）。"""

    class FlakyClips(MockVideoClipProvider):
        name = "flaky-clips"

        def __init__(self):
            super().__init__(width=WIDTH, height=HEIGHT, fps=FPS, seconds=SECONDS)
            self.calls = 0

        async def generate(self, *, image_path, prompt):
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("模拟视频模型限流")
            return await super().generate(image_path=image_path, prompt=prompt)

    client.app.dependency_overrides[get_clip_provider] = lambda: FlakyClips()
    _, episode = _prepare_with_images(client)

    task = client.post(f"/api/v1/episodes/{episode['id']}/clips", json={}).json()
    finished = client.get(f"/api/v1/tasks/{task['id']}").json()
    assert finished["status"] == "succeeded", "部分成功仍算成功"

    detail = client.get(f"/api/v1/episodes/{episode['id']}").json()
    assert any(s["clip_error"] for s in detail["shots"]), "应该有一个镜头记下失败原因"
    assert any(s["clip_path"] for s in detail["shots"]), "其它镜头应该成功"


def test_clip_quota_is_separate(client):
    from app.services.quota import QuotaService

    from app.api.deps import get_quota_service

    _use_fast_mock_clips(client)
    client.app.dependency_overrides[get_quota_service] = lambda: QuotaService(
        limits={"shots": 5, "images": 5, "clips": 1}
    )
    _, episode = _prepare_with_images(client)

    assert client.post(f"/api/v1/episodes/{episode['id']}/clips", json={}).status_code == 202
    resp = client.post(f"/api/v1/episodes/{episode['id']}/clips", json={})
    assert resp.status_code == 429
    assert "生成动态镜头" in resp.json()["error"]["message"]


def test_deleting_drama_removes_clip_files(client):
    import os

    _use_fast_mock_clips(client)
    drama, episode = _prepare_with_images(client)
    task = client.post(f"/api/v1/episodes/{episode['id']}/clips", json={}).json()
    client.get(f"/api/v1/tasks/{task['id']}")

    detail = client.get(f"/api/v1/episodes/{episode['id']}").json()
    files = [
        os.path.join(os.environ["MEDIA_ROOT"], shot["clip_path"])
        for shot in detail["shots"]
        if shot["clip_path"]
    ]
    assert files and all(os.path.isfile(f) for f in files)

    assert client.delete(f"/api/v1/dramas/{drama['id']}").status_code == 204
    assert not any(os.path.exists(f) for f in files), "删短剧时视频文件也必须一起删（体积大）"


# ───────────────────────── 真实适配器的契约测试 ─────────────────────────


def test_real_provider_contract_against_fake_vendor(tmp_path):
    """用本地假厂商验证真实适配器的完整契约：提交 -> 轮询 -> 下载。

    真实视频模型又慢又贵还可能限流，不适合放进 CI；
    但"请求发对没有、异步流程走通没有、结果解析对没有"必须被固定下来，
    否则接上真 Key 时才发现问题就太晚了。
    """
    import http.server
    import json as jsonlib
    import socketserver
    import threading

    from app.services.videos.openai_compat import OpenAICompatVideoClipProvider

    video_bytes = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 2000
    received: list[dict] = []
    polls = {"count": 0}

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def _json(self, code, payload):
            body = jsonlib.dumps(payload).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            length = int(self.headers.get("Content-Length", 0))
            received.append(
                {
                    "path": self.path,
                    "auth": self.headers.get("Authorization"),
                    "payload": jsonlib.loads(self.rfile.read(length) or b"{}"),
                }
            )
            self._json(200, {"id": "task-123", "task_status": "PROCESSING"})

        def do_GET(self):
            if self.path.startswith("/async-result/"):
                polls["count"] += 1
                # 第一次返回"处理中"，第二次才成功 —— 逼着适配器真的去轮询
                if polls["count"] < 2:
                    self._json(200, {"task_status": "PROCESSING"})
                else:
                    port = self.server.server_address[1]
                    self._json(
                        200,
                        {
                            "task_status": "SUCCESS",
                            "video_result": [{"url": f"http://127.0.0.1:{port}/files/out.mp4"}],
                        },
                    )
                return
            if self.path == "/files/out.mp4":
                self.send_response(200)
                self.send_header("Content-Type", "video/mp4")
                self.send_header("Content-Length", str(len(video_bytes)))
                self.end_headers()
                self.wfile.write(video_bytes)
                return
            self.send_response(404)
            self.end_headers()

    server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), Handler)
    server.allow_reuse_address = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    image_path = tmp_path / "still.png"
    _write_test_image(image_path)

    try:
        provider = OpenAICompatVideoClipProvider(
            api_key="fake-key",
            base_url=f"http://127.0.0.1:{server.server_address[1]}",
            model="cogvideox-3",
            size="720x1280",
            quality="speed",
            seconds=5,
            poll_interval=0.2,
            poll_timeout=10,
        )
        data = asyncio.run(provider.generate(image_path=image_path, prompt="让画面动起来"))
    finally:
        server.shutdown()

    assert data == video_bytes, "应该把厂商给的视频下载下来"
    assert polls["count"] >= 2, "应该真的轮询到成功为止"

    sent = received[0]
    assert sent["path"] == "/videos/generations"
    assert sent["auth"] == "Bearer fake-key"
    payload = sent["payload"]
    assert payload["model"] == "cogvideox-3"
    assert payload["prompt"] == "让画面动起来"
    assert payload["size"] == "720x1280"
    assert payload["duration"] == 5
    assert payload["image_url"].startswith("data:image/png;base64,"), "本地图片要以 base64 形式传"
    assert "watermark_enabled" not in payload  # 默认不传，由配置决定


def test_real_provider_reports_rate_limit_clearly(tmp_path):
    """厂商限流（429）要给出人能看懂的提示 —— 我们实测智谱免费视频模型就是 429。"""
    import http.server
    import json as jsonlib
    import socketserver
    import threading

    from app.core.errors import LLMError
    from app.services.videos.openai_compat import OpenAICompatVideoClipProvider

    class Handler(http.server.BaseHTTPRequestHandler):
        # 用 HTTP/1.1 并显式声明长度：真实厂商都是这么应答的，
        # 用默认的 HTTP/1.0 有时会让客户端在读到响应体前就断开。
        protocol_version = "HTTP/1.1"

        def log_message(self, *args):
            pass

        def do_POST(self):
            # 必须先把请求体读完再应答，否则连接状态会错乱
            length = int(self.headers.get("Content-Length", 0))
            self.rfile.read(length)
            body = jsonlib.dumps({"error": {"code": "1305", "message": "该模型当前请求量过大"}}).encode()
            self.send_response(429)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    image_path = tmp_path / "still.png"
    _write_test_image(image_path)

    try:
        provider = OpenAICompatVideoClipProvider(
            api_key="k",
            base_url=f"http://127.0.0.1:{server.server_address[1]}",
            model="cogvideox-flash",
            size="960x1280",
            poll_interval=0.2,
        )
        with pytest.raises(LLMError) as exc:
            asyncio.run(provider.generate(image_path=image_path, prompt="x"))
    finally:
        server.shutdown()

    assert "429" in str(exc.value)
    assert "限流" in str(exc.value)


def test_video_provider_factory(tmp_path):
    """工厂：none -> None；mock -> 本地运镜；openai_compat -> 真实适配器。"""
    from app.core.config import Settings
    from app.services.videos.factory import build_clip_provider
    from app.services.videos.openai_compat import OpenAICompatVideoClipProvider

    assert build_clip_provider(Settings(video_clip_provider="none")) is None
    assert isinstance(build_clip_provider(Settings(video_clip_provider="mock")), MockVideoClipProvider)

    # 没配 VIDEO_API_KEY 时应复用 IMAGE_API_KEY（智谱同一个 Key）
    provider = build_clip_provider(
        Settings(video_clip_provider="openai_compat", image_api_key="reused-key")
    )
    assert isinstance(provider, OpenAICompatVideoClipProvider)

    with pytest.raises(Exception):
        build_clip_provider(Settings(video_clip_provider="openai_compat", image_api_key=""))
