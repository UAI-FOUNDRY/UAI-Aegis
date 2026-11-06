"""one strike per excursion

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-06
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = '0003'
down_revision: str | None = '0002'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table('exam_sessions', schema=None) as batch_op:
        batch_op.add_column(sa.Column('last_excursion', sa.String(length=64), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('exam_sessions', schema=None) as batch_op:
        batch_op.drop_column('last_excursion')
