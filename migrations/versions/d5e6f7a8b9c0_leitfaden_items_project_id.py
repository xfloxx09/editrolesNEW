"""Leitfaden items: optional project_id (per-project checklist)

Revision ID: d5e6f7a8b9c0
Revises: c3d4e5f6a7b8
Create Date: 2026-04-02
"""

from alembic import op
import sqlalchemy as sa


revision = 'd5e6f7a8b9c0'
down_revision = 'c3d4e5f6a7b8'
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()
    insp = sa.inspect(conn)
    if 'leitfaden_items' not in insp.get_table_names():
        return
    cols = {c['name'] for c in insp.get_columns('leitfaden_items')}
    if 'project_id' in cols:
        return
    op.add_column(
        'leitfaden_items',
        sa.Column('project_id', sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        'leitfaden_items_project_id_fkey',
        'leitfaden_items', 'projects',
        ['project_id'], ['id'],
    )


def downgrade():
    conn = op.get_bind()
    insp = sa.inspect(conn)
    if 'leitfaden_items' not in insp.get_table_names():
        return
    cols = {c['name'] for c in insp.get_columns('leitfaden_items')}
    if 'project_id' not in cols:
        return
    op.drop_constraint('leitfaden_items_project_id_fkey', 'leitfaden_items', type_='foreignkey')
    op.drop_column('leitfaden_items', 'project_id')
