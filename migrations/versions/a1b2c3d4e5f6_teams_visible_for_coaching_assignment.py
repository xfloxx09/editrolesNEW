"""teams.visible_for_coaching_assignment for assignment-only whitelist

Revision ID: a1b2c3d4e5f6
Revises: f6a7b8c9d0e1
Create Date: 2026-04-03
"""

from alembic import op
import sqlalchemy as sa


revision = 'a1b2c3d4e5f6'
down_revision = 'f6a7b8c9d0e1'
branch_labels = None
depends_on = None


def upgrade():
    # Idempotent: create_app() may add this column before Alembic runs (startup migration).
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    cols = {c['name'] for c in inspector.get_columns('teams')}
    if 'visible_for_coaching_assignment' not in cols:
        op.add_column(
            'teams',
            sa.Column(
                'visible_for_coaching_assignment',
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            ),
        )


def downgrade():
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    cols = {c['name'] for c in inspector.get_columns('teams')}
    if 'visible_for_coaching_assignment' in cols:
        op.drop_column('teams', 'visible_for_coaching_assignment')
