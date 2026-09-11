"""rastreio (quem/quando/IP) em assembleias e pautas

Revision ID: c1f9a6e34d82
Revises: b8e3d1f76a29
"""
from alembic import op
import sqlalchemy as sa

revision = 'c1f9a6e34d82'
down_revision = 'b8e3d1f76a29'
branch_labels = None
depends_on = None


def upgrade():
    for t in ('assembleia', 'pauta'):
        op.add_column(t, sa.Column('criado_por', sa.String(length=60), nullable=True))
        op.add_column(t, sa.Column('criado_ip', sa.String(length=45), nullable=True))
        op.add_column(t, sa.Column('criado_em', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True))


def downgrade():
    for t in ('assembleia', 'pauta'):
        for c in ('criado_em', 'criado_ip', 'criado_por'):
            op.drop_column(t, c)
