"""character card fields and turnaround

Revision ID: 30d8c96a1910
Revises: ceeeb9c29f0e
Create Date: 2026-10-18 16:37:25.450829

⚠️ 自动生成的版本有**两个必然失败的问题**，这里都手工修了（都是本项目踩过的坑）：

1. **autogenerate 给 NOT NULL 新列不带 server_default**（第四次踩到了）。
   `characters` 表里已经有用户数据，加一个 NOT NULL 又没有默认值的列，
   `ALTER TABLE` 会直接失败。TEXT 列在 MySQL 8.0.13+ 还要求默认值写成表达式 `('')`，
   所以用 `sa.text("('')")`，String 列用普通字符串即可。

2. **SQLite 不支持 ALTER COLUMN / ADD CONSTRAINT**，而这次要把
   `generation_tasks.episode_id` 改成可空（剧级任务不属于任何一集）。
   所以那部分改动套了 `batch_alter_table` —— 它在 SQLite 上是"建新表 + 拷数据 +
   换名"，在 MySQL 上就是普通 ALTER，两边都能跑。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '30d8c96a1910'
down_revision: Union[str, None] = 'ceeeb9c29f0e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── ① 角色卡：给"画角色/画镜头"用的字段 ──
    op.add_column('characters', sa.Column('age', sa.Integer(), nullable=True))
    op.add_column(
        'characters',
        sa.Column('appearance', sa.Text(), nullable=False, server_default=sa.text("('')")),
    )
    op.add_column(
        'characters',
        sa.Column('occupation', sa.String(length=60), nullable=False, server_default=''),
    )
    op.add_column(
        'characters',
        sa.Column('background', sa.Text(), nullable=False, server_default=sa.text("('')")),
    )
    op.add_column(
        'characters',
        sa.Column('look_prompt', sa.Text(), nullable=False, server_default=sa.text("('')")),
    )
    op.add_column('characters', sa.Column('turnaround_path', sa.String(length=255), nullable=True))
    op.add_column('characters', sa.Column('turnaround_error', sa.Text(), nullable=True))

    # ── ② 任务表支持"剧级任务"（角色三视图不属于任何一集）──
    with op.batch_alter_table('generation_tasks') as batch:
        batch.add_column(sa.Column('drama_id', sa.Integer(), nullable=True))
        # 剧级任务的 episode_id 为空，所以必须放开非空约束
        batch.alter_column('episode_id', existing_type=sa.INTEGER(), nullable=True)
        batch.create_index('ix_generation_tasks_drama_id', ['drama_id'])
        batch.create_foreign_key(
            'fk_generation_tasks_drama_id',
            'dramas',
            ['drama_id'],
            ['id'],
            ondelete='CASCADE',
        )


def downgrade() -> None:
    with op.batch_alter_table('generation_tasks') as batch:
        batch.drop_constraint('fk_generation_tasks_drama_id', type_='foreignkey')
        batch.drop_index('ix_generation_tasks_drama_id')
        # 回退前必须先清掉剧级任务，否则 episode_id 变回非空会失败
        batch.alter_column('episode_id', existing_type=sa.INTEGER(), nullable=False)
        batch.drop_column('drama_id')

    op.drop_column('characters', 'turnaround_error')
    op.drop_column('characters', 'turnaround_path')
    op.drop_column('characters', 'look_prompt')
    op.drop_column('characters', 'background')
    op.drop_column('characters', 'occupation')
    op.drop_column('characters', 'appearance')
    op.drop_column('characters', 'age')
