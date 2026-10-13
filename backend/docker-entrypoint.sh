#!/bin/sh
# 容器启动入口：先把数据库结构升级到最新，再启动服务。
#
# 为什么在这里跑 alembic 而不是在应用代码里跑：
# 1. "启动即迁移"让部署只需要一条 docker compose up，不会忘；
# 2. alembic upgrade head 是幂等的 —— 数据库已是最新版本时它什么都不做；
# 3. 应用代码保持无副作用（AUTO_CREATE_TABLES=false），生产环境表结构完全由迁移管理。
#
# 注意：本文件在仓库里以 LF 换行符保存（.gitattributes 保证），
# 若在 Windows 上手动编辑，不要用记事本另存为 CRLF，否则容器里会报
# "/bin/sh: bad interpreter: No such file or directory"。
set -e

echo "[entrypoint] 执行数据库迁移（幂等）..."
python -m alembic upgrade head

echo "[entrypoint] 启动服务: $*"
exec "$@"
