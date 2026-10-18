"""短剧设置（改名 + 每个模态自己的模型与 API Key）的测试。

守护四件事，每一件都关系到"用户的密钥和钱"：
    ① **密钥明文绝不落库**（加密后才存）；
    ② **接口永远不回显明文**（只给掩码）；
    ③ 用户填了就用自己的配置，**不填才回退服务端**；
    ④ `base_url` 必须校验 —— 否则等于给了一个 SSRF 入口。
"""

from app.api.deps import get_session_factory_dep
from app.core.config import get_settings
from app.services import model_config

PLAIN_KEY = "sk-test-abcdefghijklmnop1234567890"


def _make_drama(client, title: str = "测试剧") -> dict:
    return client.post("/api/v1/dramas", json={"title": title, "genre": "悬疑"}).json()


def _db(client):
    """拿到测试库的会话工厂（conftest 把依赖覆盖成了临时库）。"""
    return client.app.dependency_overrides[get_session_factory_dep]()


# ───────────────────────── ① 默认 = 用服务端配置 ─────────────────────────


def test_settings_default_to_server_config(client):
    drama = _make_drama(client)
    body = client.get(f"/api/v1/dramas/{drama['id']}/settings").json()

    assert body["title"] == "测试剧"
    for kind in ("llm", "image", "tts", "video"):
        assert body[kind]["source"] == "server", f"{kind} 默认应该走服务端"
        assert body[kind]["has_key"] is False, "没填过就不该显示成已填"
        assert body[kind]["api_key_hint"] == "", "服务端 Key 的掩码也不能露出来"


# ───────────────────────── ② 存 Key：加密落库 + 不回显 ─────────────────────────


def test_user_key_is_encrypted_in_db_and_masked_in_api(client):
    drama = _make_drama(client)

    resp = client.patch(
        f"/api/v1/dramas/{drama['id']}/settings",
        json={"image": {"provider": "openai_compat", "model": "cogview-4", "api_key": PLAIN_KEY}},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()

    # 接口里：只给"已填"和掩码
    assert body["image"]["has_key"] is True
    assert body["image"]["source"] == "drama"
    assert body["image"]["model"] == "cogview-4"
    assert body["image"]["api_key_hint"].startswith("sk-tes")
    assert body["image"]["api_key_hint"].endswith("890")
    # **明文绝不能出现在响应里**（哪怕是别的字段）
    assert PLAIN_KEY not in resp.text

    # 数据库里：必须是密文
    from app.db.models import DramaModelSettings

    with _db(client)() as db:
        row = db.query(DramaModelSettings).filter_by(drama_id=drama["id"]).one()
        stored = row.config["image"]["api_key_enc"]
    assert stored.startswith("enc:v1:"), "落库的必须是密文"
    assert PLAIN_KEY not in stored, "明文绝不能出现在数据库里"


def test_saved_key_is_actually_used_for_generation(client):
    """填了 Key 之后，解析出来的配置必须用用户的 Key（这是整个功能的重点）。"""
    drama = _make_drama(client)
    client.patch(
        f"/api/v1/dramas/{drama['id']}/settings",
        json={"image": {"provider": "openai_compat", "model": "cogview-4", "api_key": PLAIN_KEY}},
    )

    from app.db.models import Drama

    with _db(client)() as db:
        target = db.get(Drama, drama["id"])
        cfg = model_config.resolve(db, target, "image", get_settings())

    assert cfg.api_key == PLAIN_KEY, "应该用用户自己的 Key"
    assert cfg.source == "drama"
    assert cfg.model == "cogview-4"


def test_clear_key_falls_back_to_server(client):
    drama = _make_drama(client)
    url = f"/api/v1/dramas/{drama['id']}/settings"
    client.patch(url, json={"image": {"api_key": PLAIN_KEY}})
    assert client.get(url).json()["image"]["has_key"] is True

    # 传空字符串 = 清除
    body = client.patch(url, json={"image": {"api_key": ""}}).json()
    assert body["image"]["has_key"] is False
    assert body["image"]["source"] == "server"


def test_updating_one_modality_does_not_touch_others(client):
    drama = _make_drama(client)
    url = f"/api/v1/dramas/{drama['id']}/settings"
    client.patch(url, json={"image": {"api_key": PLAIN_KEY}})
    body = client.patch(url, json={"tts": {"api_key": "tts-key-1234567890abcdef"}}).json()

    # 只改了 tts，image 必须保持原样 —— 否则"一次漏传就清空配置"
    assert body["image"]["has_key"] is True
    assert body["tts"]["has_key"] is True


# ───────────────────────── ③ SSRF 防护 ─────────────────────────


def test_base_url_must_be_https(client):
    drama = _make_drama(client)
    resp = client.patch(
        f"/api/v1/dramas/{drama['id']}/settings",
        json={"llm": {"base_url": "http://api.example.com/v1"}},
    )
    assert resp.status_code == 400
    assert "https" in resp.json()["error"]["message"]


def test_base_url_must_not_point_to_internal_hosts(client):
    """这是 SSRF 的核心防线：不能让服务器替用户去访问内网。"""
    drama = _make_drama(client)
    for bad in ("https://127.0.0.1/v1", "https://localhost/v1", "https://10.0.0.5/v1"):
        resp = client.patch(
            f"/api/v1/dramas/{drama['id']}/settings", json={"llm": {"base_url": bad}}
        )
        assert resp.status_code == 400, f"{bad} 应该被拒绝"
        assert "内网" in resp.json()["error"]["message"] or "本机" in resp.json()["error"]["message"]


def test_valid_https_base_url_is_accepted(client):
    drama = _make_drama(client)
    resp = client.patch(
        f"/api/v1/dramas/{drama['id']}/settings",
        json={"llm": {"base_url": "https://api.deepseek.com/v1"}},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["llm"]["base_url"] == "https://api.deepseek.com/v1"


# ───────────────────────── ④ 其它行为 ─────────────────────────


def test_title_can_be_edited_from_settings(client):
    drama = _make_drama(client)
    body = client.patch(
        f"/api/v1/dramas/{drama['id']}/settings", json={"title": "改过名字的剧"}
    ).json()
    assert body["title"] == "改过名字的剧"

    listed = client.get("/api/v1/dramas").json()
    stored = next(d for d in listed if d["id"] == drama["id"])
    assert stored["title"] == "改过名字的剧"


def test_empty_title_is_rejected(client):
    drama = _make_drama(client)
    resp = client.patch(f"/api/v1/dramas/{drama['id']}/settings", json={"title": "   "})
    assert resp.status_code == 400


def test_settings_of_other_user_is_404(client, login_as):
    drama = _make_drama(client)
    token = login_as("bob")
    client.headers["Authorization"] = f"Bearer {token}"
    assert client.get(f"/api/v1/dramas/{drama['id']}/settings").status_code == 404
    assert (
        client.patch(
            f"/api/v1/dramas/{drama['id']}/settings", json={"title": "偷改"}
        ).status_code
        == 404
    )


def test_strict_mode_requires_user_key(client, monkeypatch):
    """服务端关掉"回退默认 Key"之后，没填 Key 的短剧必须被拒绝。

    这是"上线到公网"时必须打开的开关 —— 否则陌生人能刷爆站长的账户。
    """
    drama = _make_drama(client)
    monkeypatch.setattr(get_settings(), "allow_server_key_fallback", False)

    episode = client.post(
        f"/api/v1/dramas/{drama['id']}/episodes", json={"title": "第1集"}
    ).json()
    client.post(f"/api/v1/episodes/{episode['id']}/generate", json={"shot_count": 1})

    resp = client.post(f"/api/v1/episodes/{episode['id']}/images", json={})
    assert resp.status_code == 400, resp.text
    assert resp.json()["error"]["code"] == "api_key_required"

    # 填上 Key 之后就能用了
    client.patch(
        f"/api/v1/dramas/{drama['id']}/settings", json={"image": {"api_key": PLAIN_KEY}}
    )
    assert client.post(f"/api/v1/episodes/{episode['id']}/images", json={}).status_code == 202
