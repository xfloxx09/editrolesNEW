"""Add Abteilungen (departments) and FK on projects/users

Revision ID: c3d4e5f6a7b8
Revises: a1b2c3d4e5f6
Create Date: 2026-04-03
"""

from alembic import op
import sqlalchemy as sa


revision = 'c3d4e5f6a7b8'
down_revision = 'a1b2c3d4e5f6'
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()
    insp = sa.inspect(conn)
    if 'abteilungen' not in insp.get_table_names():
        op.create_table(
            'abteilungen',
            sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
            sa.Column('name', sa.String(length=150), nullable=False),
            sa.Column('description', sa.String(length=500), nullable=True),
            sa.PrimaryKeyConstraint('id'),
            sa.UniqueConstraint('name'),
        )
    insp = sa.inspect(conn)
    if 'projects' in insp.get_table_names():
        pc = {c['name'] for c in insp.get_columns('projects')}
        if 'abteilung_id' not in pc:
            op.add_column(
                'projects',
                sa.Column('abteilung_id', sa.Integer(), nullable=True),
            )
            op.create_foreign_key(
                'projects_abteilung_id_fkey',
                'projects', 'abteilungen',
                ['abteilung_id'], ['id'],
            )
    insp = sa.inspect(conn)
    uc = {c['name'] for c in insp.get_columns('users')} if 'users' in insp.get_table_names() else set()
    if 'users' in insp.get_table_names() and 'abteilung_id' not in uc:
        op.add_column(
            'users',
            sa.Column('abteilung_id', sa.Integer(), nullable=True),
        )
        op.create_foreign_key(
            'users_abteilung_id_fkey',
            'users', 'abteilungen',
            ['abteilung_id'], ['id'],
        )


def downgrade():
    conn = op.get_bind()
    insp = sa.inspect(conn)
    uc = {c['name'] for c in insp.get_columns('users')} if 'users' in insp.get_table_names() else set()
    if 'abteilung_id' in uc:
        op.drop_constraint('users_abteilung_id_fkey', 'users', type_='foreignkey')
        op.drop_column('users', 'abteilung_id')
    pc = {c['name'] for c in insp.get_columns('projects')} if 'projects' in insp.get_table_names() else set()
    if 'abteilung_id' in pc:
        op.drop_constraint('projects_abteilung_id_fkey', 'projects', type_='foreignkey')
        op.drop_column('projects', 'abteilung_id')
    if 'abteilungen' in insp.get_table_names():
        op.drop_table('abteilungen')
