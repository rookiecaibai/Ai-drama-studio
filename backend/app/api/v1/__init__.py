"""v1 版本路由聚合。

为什么要 /api/v1 这种版本前缀：
接口一旦对外（尤其是给前端 App 用），就不能随便改字段名。
留一个 v1 段位，将来不兼容的改动可以并行发布 v2，老客户端不会立刻崩。
"""

from fastapi import APIRouter

from app.api.v1 import auth, characters, drama_settings, dramas, episodes, health, shots, tasks

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(auth.router)
api_router.include_router(dramas.router)
api_router.include_router(episodes.router)
api_router.include_router(characters.router)
api_router.include_router(shots.router)
api_router.include_router(tasks.router)
api_router.include_router(drama_settings.router)
