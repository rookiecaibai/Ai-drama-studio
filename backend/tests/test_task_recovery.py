"""服务重启后的任务对账。

背景：任务状态存在数据库里，但"正在执行"只存在于**进程内存**中。
服务被中断（崩溃/按停止键/部署重启）后，那些任务会永远停在 running ——
前端一直转圈，用户以为还在跑。启动时必须对账，把它们标记为失败。
"""

from app.api.deps import get_session_factory_dep
from app.db.models import GenerationTask
from app.services.task_service import mark_interrupted_tasks


def _make_running_task(client, episode_id: int) -> int:
    """直接往库里插一条"卡在 running"的任务，模拟上次被中断的运行。"""
    factory = client.app.dependency_overrides[get_session_factory_dep]()
    user_id = client.get("/api/v1/auth/me").json()["id"]
    with factory() as db:
        task = GenerationTask(
            user_id=user_id,
            episode_id=episode_id,
            kind="video",
            provider="ffmpeg",
            status="running",
            shot_count=0,
        )
        db.add(task)
        db.commit()
        return task.id


def _setup(client) -> int:
    drama = client.post("/api/v1/dramas", json={"title": "断电测试", "genre": "都市"}).json()
    episode = client.post(f"/api/v1/dramas/{drama['id']}/episodes", json={"title": "第1集"}).json()
    return episode["id"]


def test_interrupted_running_task_is_marked_failed_on_startup(client):
    task_id = _make_running_task(client, _setup(client))

    # 重启前：前端看到的是"生成中"，而且永远不会结束
    assert client.get(f"/api/v1/tasks/{task_id}").json()["status"] == "running"

    factory = client.app.dependency_overrides[get_session_factory_dep]()
    cleaned = mark_interrupted_tasks(factory)
    assert cleaned >= 1

    detail = client.get(f"/api/v1/tasks/{task_id}").json()
    assert detail["status"] == "failed"
    assert "中断" in detail["error"]  # 错误信息要能解释"为什么失败"


def test_pending_task_is_also_cleaned(client):
    """排队中（pending）的任务同样不可能有人在跑了。"""
    episode_id = _setup(client)
    factory = client.app.dependency_overrides[get_session_factory_dep]()
    user_id = client.get("/api/v1/auth/me").json()["id"]
    with factory() as db:
        task = GenerationTask(
            user_id=user_id,
            episode_id=episode_id,
            kind="images",
            provider="mock",
            status="pending",
            shot_count=0,
        )
        db.add(task)
        db.commit()
        task_id = task.id

    mark_interrupted_tasks(factory)
    assert client.get(f"/api/v1/tasks/{task_id}").json()["status"] == "failed"


def test_finished_tasks_are_not_touched(client):
    """已经结束的任务不能被对账误伤。"""
    from app.api.deps import get_session_factory_dep as dep

    episode_id = _setup(client)
    factory = client.app.dependency_overrides[dep]()
    user_id = client.get("/api/v1/auth/me").json()["id"]
    with factory() as db:
        done = GenerationTask(
            user_id=user_id,
            episode_id=episode_id,
            kind="shots",
            provider="mock",
            status="succeeded",
            shot_count=6,
        )
        db.add(done)
        db.commit()
        done_id = done.id

    mark_interrupted_tasks(factory)

    detail = client.get(f"/api/v1/tasks/{done_id}").json()
    assert detail["status"] == "succeeded"
    assert detail["error"] is None
