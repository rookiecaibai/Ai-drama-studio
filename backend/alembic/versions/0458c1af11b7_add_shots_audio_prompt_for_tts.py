"""add shots.audio_prompt for TTS

Revision ID: 0458c1af11b7
Revises: 6e3ce928223b
Create Date: 2026-10-13 11:24:01.796460

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0458c1af11b7'
down_revision: Union[str, None] = '6e3ce928223b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 注意：autogenerate 生成的原始版本是 nullable=False 且没有 server_default。
    # 如果表里已有数据，直接加 NOT NULL 无默认值的列会在生产库上直接失败。
    # 所以手动补了 server_default="" —— 这就是"自动生成的迁移必须人工 review"的原因。
    op.add_column('shots', sa.Column('audio_prompt', sa.Text(), nullable=False, server_default=""))


def downgrade() -> None:
    op.drop_column('shots', 'audio_prompt')
