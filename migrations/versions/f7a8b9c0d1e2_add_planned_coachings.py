"""Add planned_coachings (Geplante Coachings)

Revision ID: f7a8b9c0d1e2
Revises: e6f7a8b9c0d1
Create Date: 2026-04-02
"""
from alembic import op
import sqlalchemy as sa


revision = 'f7a8b9c0d1e2'
down_revision = 'e6f7a8b9c0d1'
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()

    def table_names():
        return set(sa.inspect(conn).get_table_names())

    if 'planned_coachings' not in table_names():
        op.create_table(
            'planned_coachings',
            sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
            sa.Column('team_member_id', sa.Integer(), nullable=False),
            sa.Column('coach_id', sa.Integer(), nullable=False),
            sa.Column('project_id', sa.Integer(), nullable=True),
            sa.Column('team_id', sa.Integer(), nullable=True),
            sa.Column('planned_for_date', sa.Date(), nullable=False),
            sa.Column('notes', sa.Text(), nullable=True),
            sa.Column('has_verabredung', sa.Boolean(), nullable=False, server_default='0'),
            sa.Column('verabredung_text', sa.Text(), nullable=True),
            sa.Column('source_coaching_id', sa.Integer(), nullable=True),
            sa.Column('status', sa.String(length=20), nullable=False, server_default='open'),
            sa.Column('fulfilled_coaching_id', sa.Integer(), nullable=True),
            sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
            sa.ForeignKeyConstraint(['team_member_id'], ['team_members.id']),
            sa.ForeignKeyConstraint(['coach_id'], ['users.id']),
            sa.ForeignKeyConstraint(['project_id'], ['projects.id']),
            sa.ForeignKeyConstraint(['team_id'], ['teams.id']),
            sa.ForeignKeyConstraint(['source_coaching_id'], ['coachings.id']),
            sa.ForeignKeyConstraint(['fulfilled_coaching_id'], ['coachings.id']),
            sa.PrimaryKeyConstraint('id'),
        )


def downgrade():
    conn = op.get_bind()
    if 'planned_coachings' in set(sa.inspect(conn).get_table_names()):
        op.drop_table('planned_coachings')
