"""question files and blocked apps

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-08
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = '0004'
down_revision: str | None = '0003'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Admin-managed list of apps students must not run during exams.
    op.create_table('blocked_apps',
    sa.Column('process', sa.String(length=64), nullable=False),
    sa.Column('label', sa.String(length=80), nullable=False),
    sa.Column('action', sa.String(length=16), nullable=False),
    sa.Column('created_by', sa.Uuid(), nullable=True),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], name=op.f('fk_blocked_apps_created_by_users'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_blocked_apps')),
    sa.UniqueConstraint('process', name=op.f('uq_blocked_apps_process'))
    )
    # Data files that come with a question (the bytes are on disk, see services/files.py).
    op.create_table('question_files',
    sa.Column('question_id', sa.Uuid(), nullable=False),
    sa.Column('name', sa.String(length=100), nullable=False),
    sa.Column('storage_key', sa.String(length=64), nullable=False),
    sa.Column('size_bytes', sa.BigInteger(), nullable=False),
    sa.Column('sha256', sa.String(length=64), nullable=False),
    sa.Column('kind', sa.String(length=16), nullable=False),
    sa.Column('meta', sa.JSON(), nullable=False),
    sa.Column('created_by', sa.Uuid(), nullable=True),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], name=op.f('fk_question_files_created_by_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['question_id'], ['questions.id'], name=op.f('fk_question_files_question_id_questions'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_question_files')),
    sa.UniqueConstraint('question_id', 'name', name=op.f('uq_question_files_question_id_name')),
    sa.UniqueConstraint('storage_key', name=op.f('uq_question_files_storage_key'))
    )


def downgrade() -> None:
    op.drop_table('question_files')
    op.drop_table('blocked_apps')
