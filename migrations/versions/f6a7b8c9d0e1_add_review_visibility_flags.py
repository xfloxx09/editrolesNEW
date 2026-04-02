"""add review visibility flags for coach vs manager lists

Revision ID: f6a7b8c9d0e1
Revises: c4e9a1b2d3f4
Create Date: 2026-04-02
"""

from alembic import op
import sqlalchemy as sa


revision = 'f6a7b8c9d0e1'
down_revision = 'c4e9a1b2d3f4'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        'coaching_reviews',
        sa.Column(
            'visible_to_coach',
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
    )
    op.add_column(
        'coaching_reviews',
        sa.Column(
            'visible_to_manager',
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
    )


def downgrade():
    op.drop_column('coaching_reviews', 'visible_to_manager')
    op.drop_column('coaching_reviews', 'visible_to_coach')

