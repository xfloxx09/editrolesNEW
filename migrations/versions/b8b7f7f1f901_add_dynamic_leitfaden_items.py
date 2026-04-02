"""add dynamic leitfaden items

Revision ID: b8b7f7f1f901
Revises: f62172b38762
Create Date: 2026-04-02 15:45:00.000000
"""
from alembic import op
import sqlalchemy as sa
from datetime import datetime


# revision identifiers, used by Alembic.
revision = 'b8b7f7f1f901'
down_revision = 'f62172b38762'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'leitfaden_items',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=120), nullable=False),
        sa.Column('position', sa.Integer(), nullable=False),
        sa.Column('is_active', sa.Boolean(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_table(
        'coaching_leitfaden_responses',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('coaching_id', sa.Integer(), nullable=False),
        sa.Column('item_id', sa.Integer(), nullable=False),
        sa.Column('value', sa.String(length=10), nullable=False),
        sa.ForeignKeyConstraint(['coaching_id'], ['coachings.id'], ),
        sa.ForeignKeyConstraint(['item_id'], ['leitfaden_items.id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('coaching_id', 'item_id', name='uq_coaching_leitfaden_item')
    )

    op.bulk_insert(
        sa.table(
            'leitfaden_items',
            sa.column('name', sa.String),
            sa.column('position', sa.Integer),
            sa.column('is_active', sa.Boolean),
            sa.column('created_at', sa.DateTime),
        ),
        [
            {'name': 'Begrüßung', 'position': 1, 'is_active': True, 'created_at': datetime.utcnow()},
            {'name': 'Legitimation', 'position': 2, 'is_active': True, 'created_at': datetime.utcnow()},
            {'name': 'PKA', 'position': 3, 'is_active': True, 'created_at': datetime.utcnow()},
            {'name': 'KEK', 'position': 4, 'is_active': True, 'created_at': datetime.utcnow()},
            {'name': 'Angebot', 'position': 5, 'is_active': True, 'created_at': datetime.utcnow()},
            {'name': 'Zusammenfassung', 'position': 6, 'is_active': True, 'created_at': datetime.utcnow()},
            {'name': 'KZB', 'position': 7, 'is_active': True, 'created_at': datetime.utcnow()},
        ]
    )


def downgrade():
    op.drop_table('coaching_leitfaden_responses')
    op.drop_table('leitfaden_items')
