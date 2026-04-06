"""Coaching-Bogen: Themen, Layout, coaching_subject length

Revision ID: e6f7a8b9c0d1
Revises: d5e6f7a8b9c0
Create Date: 2026-04-02
"""
from alembic import op
import sqlalchemy as sa
from datetime import datetime


revision = 'e6f7a8b9c0d1'
down_revision = 'd5e6f7a8b9c0'
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()

    def table_names():
        return set(sa.inspect(conn).get_table_names())

    if 'coaching_thema_items' not in table_names():
        op.create_table(
            'coaching_thema_items',
            sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
            sa.Column('name', sa.String(length=120), nullable=False),
            sa.Column('position', sa.Integer(), nullable=False, server_default='0'),
            sa.Column('is_active', sa.Boolean(), nullable=False, server_default='1'),
            sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
            sa.Column('project_id', sa.Integer(), nullable=True),
            sa.ForeignKeyConstraint(['project_id'], ['projects.id']),
            sa.PrimaryKeyConstraint('id'),
        )
    if 'coaching_bogen_layouts' not in table_names():
        op.create_table(
            'coaching_bogen_layouts',
            sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
            sa.Column('project_id', sa.Integer(), nullable=True),
            sa.Column('show_performance_bar', sa.Boolean(), nullable=False, server_default='1'),
            sa.Column('show_coach_notes', sa.Boolean(), nullable=False, server_default='1'),
            sa.Column('show_time_spent', sa.Boolean(), nullable=False, server_default='1'),
            sa.Column('allow_side_by_side', sa.Boolean(), nullable=False, server_default='1'),
            sa.Column('allow_tcap', sa.Boolean(), nullable=False, server_default='1'),
            sa.ForeignKeyConstraint(['project_id'], ['projects.id']),
            sa.PrimaryKeyConstraint('id'),
        )

    if 'coachings' in table_names():
        cols = {c['name']: c for c in sa.inspect(conn).get_columns('coachings')}
        if 'coaching_subject' in cols:
            try:
                op.alter_column(
                    'coachings',
                    'coaching_subject',
                    existing_type=sa.String(length=50),
                    type_=sa.String(length=120),
                    existing_nullable=True,
                )
            except Exception:
                pass

    if 'coaching_bogen_layouts' in table_names():
        res = conn.execute(sa.text('SELECT COUNT(*) FROM coaching_bogen_layouts WHERE project_id IS NULL')).scalar()
        if not res:
            op.bulk_insert(
                sa.table(
                    'coaching_bogen_layouts',
                    sa.column('project_id', sa.Integer),
                    sa.column('show_performance_bar', sa.Boolean),
                    sa.column('show_coach_notes', sa.Boolean),
                    sa.column('show_time_spent', sa.Boolean),
                    sa.column('allow_side_by_side', sa.Boolean),
                    sa.column('allow_tcap', sa.Boolean),
                ),
                [{
                    'project_id': None,
                    'show_performance_bar': True,
                    'show_coach_notes': True,
                    'show_time_spent': True,
                    'allow_side_by_side': True,
                    'allow_tcap': True,
                }],
            )

    if 'coaching_thema_items' in table_names():
        res2 = conn.execute(sa.text('SELECT COUNT(*) FROM coaching_thema_items')).scalar()
        if not res2:
            now = datetime.utcnow()
            op.bulk_insert(
                sa.table(
                    'coaching_thema_items',
                    sa.column('name', sa.String),
                    sa.column('position', sa.Integer),
                    sa.column('is_active', sa.Boolean),
                    sa.column('created_at', sa.DateTime),
                    sa.column('project_id', sa.Integer),
                ),
                [
                    {'name': 'Sales', 'position': 1, 'is_active': True, 'created_at': now, 'project_id': None},
                    {'name': 'Qualität', 'position': 2, 'is_active': True, 'created_at': now, 'project_id': None},
                    {'name': 'Allgemein', 'position': 3, 'is_active': True, 'created_at': now, 'project_id': None},
                ],
            )


def downgrade():
    conn = op.get_bind()
    insp = sa.inspect(conn)
    if 'coaching_thema_items' in insp.get_table_names():
        op.drop_table('coaching_thema_items')
    if 'coaching_bogen_layouts' in insp.get_table_names():
        op.drop_table('coaching_bogen_layouts')
    if 'coachings' in insp.get_table_names():
        try:
            op.alter_column(
                'coachings',
                'coaching_subject',
                existing_type=sa.String(length=120),
                type_=sa.String(length=50),
                existing_nullable=True,
            )
        except Exception:
            pass
