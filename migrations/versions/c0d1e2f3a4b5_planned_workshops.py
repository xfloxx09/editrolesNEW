"""Add planned_workshops

Revision ID: c0d1e2f3a4b5
Revises: a8b9c0d1e2f3
Create Date: 2026-04-02
"""
from alembic import op
import sqlalchemy as sa


revision = 'c0d1e2f3a4b5'
down_revision = 'a8b9c0d1e2f3'
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()
    if 'planned_workshops' not in set(sa.inspect(conn).get_table_names()):
        op.create_table(
            'planned_workshops',
            sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
            sa.Column('coach_id', sa.Integer(), nullable=False),
            sa.Column('project_id', sa.Integer(), nullable=True),
            sa.Column('title', sa.String(length=200), nullable=False),
            sa.Column('planned_for_date', sa.Date(), nullable=False),
            sa.Column('notes', sa.Text(), nullable=True),
            sa.Column('status', sa.String(length=20), nullable=False, server_default='open'),
            sa.Column('fulfilled_workshop_id', sa.Integer(), nullable=True),
            sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
            sa.ForeignKeyConstraint(['coach_id'], ['users.id']),
            sa.ForeignKeyConstraint(['project_id'], ['projects.id']),
            sa.ForeignKeyConstraint(['fulfilled_workshop_id'], ['workshops.id']),
            sa.PrimaryKeyConstraint('id'),
        )


def downgrade():
    conn = op.get_bind()
    if 'planned_workshops' in set(sa.inspect(conn).get_table_names()):
        op.drop_table('planned_workshops')
