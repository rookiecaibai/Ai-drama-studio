def test_health_ok(client):
    resp = client.get("/api/v1/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["llm_provider"] == "mock"


def test_openapi_available(client):
    """接口文档能被生成，说明所有 response_model 都是合法的。"""
    resp = client.get("/openapi.json")
    assert resp.status_code == 200
    paths = resp.json()["paths"]
    assert "/api/v1/dramas" in paths
    assert "/api/v1/episodes/{episode_id}/generate" in paths


def test_docs_uses_local_assets(client):
    """/docs 必须引用本地静态文件，不能依赖境外 CDN。

    这是一个"回归测试"：它锁住一个我们真实踩过的坑。

    背景：FastAPI 默认的 /docs 会去 cdn.jsdelivr.net 加载 swagger-ui-bundle.js。
    国内网络下这个 CDN 连不上，结果是 HTML 返回 200、但页面一片空白，
    而且服务端日志里看不出任何异常 —— 排查成本很高。
    所以这里断言"HTML 里必须出现本地路径、且不能出现 cdn.jsdelivr.net"，
    防止将来某次重构把 CDN 又引回来。
    """
    resp = client.get("/docs")
    assert resp.status_code == 200
    body = resp.text
    assert "/static/swagger-ui/swagger-ui-bundle.js" in body
    assert "/static/swagger-ui/swagger-ui.css" in body
    assert "cdn.jsdelivr.net" not in body, "Swagger UI 又依赖境外 CDN 了，离线环境会白屏"


def test_docs_assets_are_actually_served(client):
    """静态资源真的能被访问到，而且内容不是空的。"""
    js = client.get("/static/swagger-ui/swagger-ui-bundle.js")
    assert js.status_code == 200
    assert len(js.content) > 100_000, "swagger-ui-bundle.js 看起来不是完整文件"

    css = client.get("/static/swagger-ui/swagger-ui.css")
    assert css.status_code == 200
    assert len(css.content) > 10_000

    redoc = client.get("/static/redoc/redoc.standalone.js")
    assert redoc.status_code == 200
    assert len(redoc.content) > 100_000


def test_redoc_also_local(client):
    resp = client.get("/redoc")
    assert resp.status_code == 200
    assert "/static/redoc/redoc.standalone.js" in resp.text
    assert "cdn.jsdelivr.net" not in resp.text

