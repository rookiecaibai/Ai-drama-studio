"""Alembic 迁移环境。

为什么这么写（三条关键决策）：

1. 数据库地址从应用配置读取，而不是写死在 alembic.ini：
   `get_settings().database_url` 和业务代码用同一个来源，
   所以在哪台机器上跑，迁移就作用于哪台机器的库。

2. `from app.db import models` 必须导入：
   Base.metadata 是"空白的黑板"，只有导入 models 模块，
   表结构才会注册进去，autogenerate（自动比对生成迁移）才知道目标长什么样。

3. 迁移脚本本身就是"版本化的表结构历史"：
   每次改模型就生成一个新脚本（revision），可以 review、可以回滚，
   这就是 create_all 做不到、而生产环境必须有的东西。
"""

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from app.core.config import get_settings
from app.db import models  # noqa: F401 —— 让表结构注册进 Base.metadata
from app.db.base import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# 关键一行：把应用的数据库地址注入 alembic
config.set_main_option("sqlalchemy.url", get_settings().database_url)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """离线模式：只生成 SQL 文本，不真正连接数据库（用于 review / 手动执行）。"""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """在线模式：正常连数据库执行迁移。"""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
