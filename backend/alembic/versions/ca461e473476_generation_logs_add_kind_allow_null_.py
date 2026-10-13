"""generation_logs: add kind, allow null episode_id

Revision ID: ca461e473476
Revises: 2c197a8b7f02
Create Date: 2026-10-13 14:25:59.219028

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'ca461e473476'
down_revision: Union[str, None] = '2c197a8b7f02'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 注意 1：autogenerate 生成的是 nullable=False 且没有默认值 ——
    # 表里已有数据时这种迁移必然失败。手动补 server_default='shots'
    # （既有的流水记录都是生成分镜，语义上也正确）。
    op.add_column(
        "generation_logs",
        sa.Column("kind", sa.String(length=20), nullable=False, server_default="shots"),
    )

    # 注意 2：SQLite 不支持 ALTER COLUMN，必须用 batch_alter_table
    # 走"建新表 -> 拷贝数据 -> 删旧表 -> 改名"的方式。
    # batch 模式在 MySQL 上会退化成普通的 ALTER TABLE，所以两种数据库都能用同一份脚本。
    with op.batch_alter_table("generation_logs") as batch_op:
        batch_op.alter_column("episode_id", existing_type=sa.INTEGER(), nullable=True)


def downgrade() -> None:
    # 回滚前要保证 episode_id 没有 NULL，否则改回 NOT NULL 会失败
    # （真实项目里这一步通常会先删掉/补齐那些行）。
    with op.batch_alter_table("generation_logs") as batch_op:
        batch_op.alter_column("episode_id", existing_type=sa.INTEGER(), nullable=False)
    op.drop_column("generation_logs", "kind")
