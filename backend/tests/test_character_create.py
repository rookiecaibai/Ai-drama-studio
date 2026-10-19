"""手动新增角色的接口测试。

为什么要有这个功能：角色表原来是**纯 AI 分析**（扫台词判断谁在说话），两个死穴：
    ① AI 只认得出**有台词**的人 —— 背景里的保安、不出声的路人它发现不了；
    ② "第 5 集突然来了个新角色"，AI 还没分析过，用户只能干等。

守护四件事：
    ① 建完就能用：配音参数（音色/音高/语速）自动按性别+年龄推出来，不用再跑 AI；
    ② 年龄段能按年龄自动落位；
    ③ 同剧重名必须被拦住（重名会破坏台词里「角色名：」的说话人匹配）；
    ④ 别人的短剧不能塞角色进来。
"""

from app.services.character_look import derive_age_group
from app.services.voice_profile import profile_for


def _make_drama(client, title: str = "测试剧") -> dict:
    resp = client.post("/api/v1/dramas", json={"title": title, "genre": "悬疑"})
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_create_character_fills_voice_profile(client):
    """新建角色时自动配好音色 —— 这样加完就能直接配音/画三视图。"""
    drama = _make_drama(client)
    resp = client.post(
        f"/api/v1/dramas/{drama['id']}/characters",
        json={
            "name": "王警官",
            "gender": "male",
            "age": 52,
            "appearance": "短发、微胖、深蓝夹克",
            "occupation": "刑警",
            "background": "干了二十年刑警",
        },
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()

    expected_age_group = derive_age_group(52)
    expected = profile_for("male", expected_age_group)

    assert body["name"] == "王警官"
    assert body["age"] == 52
    assert body["age_group"] == expected_age_group, "年龄段应该按年龄自动推导"
    assert body["voice"] == expected.voice, "音色应该按性别+年龄段自动选"
    assert body["pitch_shift"] == expected.pitch_shift
    assert body["speed"] == expected.speed
    assert body["turnaround_path"] is None  # 三视图还没画

    # 列表里能看到
    listing = client.get(f"/api/v1/dramas/{drama['id']}/characters").json()
    assert [c["name"] for c in listing] == ["王警官"]


def test_explicit_age_group_wins_over_age(client):
    """给的是"看起来很年轻的中年人"时，用户可以显式指定年龄段。"""
    drama = _make_drama(client)
    resp = client.post(
        f"/api/v1/dramas/{drama['id']}/characters",
        json={"name": "老张", "gender": "male", "age": 40, "age_group": "elderly"},
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["age_group"] == "elderly"


def test_duplicate_name_is_rejected(client):
    """同剧重名必须 409 —— 重名会让台词里的说话人匹配到错的人。"""
    drama = _make_drama(client)
    payload = {"name": "李四", "gender": "male"}
    assert client.post(f"/api/v1/dramas/{drama['id']}/characters", json=payload).status_code == 201

    resp = client.post(f"/api/v1/dramas/{drama['id']}/characters", json=payload)
    assert resp.status_code == 409, resp.text
    assert "李四" in resp.text


def test_blank_name_is_rejected(client):
    """纯空格的名字要 400。Pydantic 的 min_length 拦不住空格，所以后端自己 strip 再判。"""
    drama = _make_drama(client)
    resp = client.post(f"/api/v1/dramas/{drama['id']}/characters", json={"name": "   "})
    assert resp.status_code == 400, resp.text


def test_cannot_add_character_to_someone_elses_drama(client, login_as):
    """别人的短剧不能塞角色 —— 越权必须 404（不是 403，避免泄露"这部剧存在"）。"""
    drama = _make_drama(client)
    bob_token = login_as("bob")
    resp = client.post(
        f"/api/v1/dramas/{drama['id']}/characters",
        json={"name": "入侵者"},
        headers={"Authorization": f"Bearer {bob_token}"},
    )
    assert resp.status_code == 404, resp.text
