"""add coaching_reviews for employee feedback on coaches

Revision ID: c4e9a1b2d3f4
Revises: b8b7f7f1f901
Create Date: 2026-04-02
"""
from alembic import op
import sqlalchemy as sa


revision = 'c4e9a1b2d3f4'
down_revision = 'b8b7f7f1f901'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'coaching_reviews',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('coaching_id', sa.Integer(), nullable=False),
        sa.Column('reviewer_user_id', sa.Integer(), nullable=False),
        sa.Column('rating', sa.Integer(), nullable=False),
        sa.Column('comment', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['coaching_id'], ['coachings.id'], ),
        sa.ForeignKeyConstraint(['reviewer_user_id'], ['users.id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('coaching_id', name='uq_coaching_review_coaching_id')
    )


def downgrade():
    op.drop_table('coaching_reviews')
