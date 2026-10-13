"""shots: image_path/image_error; generation_tasks: kind/total/done

Revision ID: 8702be49095c
Revises: ca461e473476
Create Date: 2026-10-13 14:49:09.190394

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '8702be49095c'
down_revision: Union[str, None] = 'ca461e473476'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 又一次踩到同一个坑（已是第三次）：autogenerate 给 NOT NULL 列**不带 server_default**，
    # 表里只要有数据，这条迁移就会失败。凡是要给已有数据的表加 NOT NULL 列，都必须补默认值。
    op.add_column(
        "generation_tasks",
        sa.Column("kind", sa.String(length=20), nullable=False, server_default="shots"),
    )
    op.add_column(
        "generation_tasks",
        sa.Column("total", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "generation_tasks",
        sa.Column("done", sa.Integer(), nullable=False, server_default="0"),
    )
    # 这两列是可空的（老数据就是"还没有画面"），所以不需要默认值
    op.add_column("shots", sa.Column("image_path", sa.String(length=255), nullable=True))
    op.add_column("shots", sa.Column("image_error", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("shots", "image_error")
    op.drop_column("shots", "image_path")
    op.drop_column("generation_tasks", "done")
    op.drop_column("generation_tasks", "total")
    op.drop_column("generation_tasks", "kind")
