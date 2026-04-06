"""planned_coachings: verabredung_erfuellt

Revision ID: a8b9c0d1e2f3
Revises: f7a8b9c0d1e2
Create Date: 2026-04-02
"""
from alembic import op
import sqlalchemy as sa


revision = 'a8b9c0d1e2f3'
down_revision = 'f7a8b9c0d1e2'
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()
    insp = sa.inspect(conn)
    cols = {c['name'] for c in insp.get_columns('planned_coachings')} if 'planned_coachings' in insp.get_table_names() else set()
    if 'verabredung_erfuellt' not in cols:
        op.add_column(
            'planned_coachings',
            sa.Column('verabredung_erfuellt', sa.Boolean(), nullable=True),
        )


def downgrade():
    conn = op.get_bind()
    insp = sa.inspect(conn)
    if 'planned_coachings' not in insp.get_table_names():
        return
    cols = {c['name'] for c in insp.get_columns('planned_coachings')}
    if 'verabredung_erfuellt' in cols:
        op.drop_column('planned_coachings', 'verabredung_erfuellt')
