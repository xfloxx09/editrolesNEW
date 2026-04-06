"""assigned_coachings: rejection_reason

Revision ID: e1f2a3b4c5d6
Revises: c0d1e2f3a4b5
Create Date: 2026-04-06
"""
from alembic import op
import sqlalchemy as sa


revision = 'e1f2a3b4c5d6'
down_revision = 'c0d1e2f3a4b5'
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()
    insp = sa.inspect(conn)
    if 'assigned_coachings' not in insp.get_table_names():
        return
    cols = {c['name'] for c in insp.get_columns('assigned_coachings')}
    if 'rejection_reason' not in cols:
        op.add_column('assigned_coachings', sa.Column('rejection_reason', sa.Text(), nullable=True))


def downgrade():
    conn = op.get_bind()
    insp = sa.inspect(conn)
    if 'assigned_coachings' not in insp.get_table_names():
        return
    cols = {c['name'] for c in insp.get_columns('assigned_coachings')}
    if 'rejection_reason' in cols:
        op.drop_column('assigned_coachings', 'rejection_reason')
