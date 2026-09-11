"""rastreio (quem/quando/IP) nos usuários da administração

Revision ID: d4b7c9e15f60
Revises: c1f9a6e34d82
"""
from alembic import op
import sqlalchemy as sa

revision = 'd4b7c9e15f60'
down_revision = 'c1f9a6e34d82'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('admin_user', sa.Column('criado_por', sa.String(length=60), nullable=True))
    op.add_column('admin_user', sa.Column('criado_ip', sa.String(length=45), nullable=True))
    op.add_column('admin_user', sa.Column('criado_em', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True))
    op.add_column('admin_user', sa.Column('alterado_por', sa.String(length=60), nullable=True))
    op.add_column('admin_user', sa.Column('alterado_ip', sa.String(length=45), nullable=True))
    op.add_column('admin_user', sa.Column('alterado_em', sa.DateTime(timezone=True), nullable=True))


def downgrade():
    for c in ('alterado_em', 'alterado_ip', 'alterado_por', 'criado_em', 'criado_ip', 'criado_por'):
        op.drop_column('admin_user', c)
