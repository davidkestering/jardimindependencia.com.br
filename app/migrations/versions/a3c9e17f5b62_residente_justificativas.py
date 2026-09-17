"""justificativa obrigatória do condômino ao remover residente e ao transferir o acesso

Revision ID: a3c9e17f5b62
Revises: f7b2d9e63a18
"""
from alembic import op
import sqlalchemy as sa

revision = 'a3c9e17f5b62'
down_revision = 'f7b2d9e63a18'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('residente', sa.Column('excluido_motivo', sa.String(length=500), nullable=True))
    op.add_column('morador', sa.Column('transferido_motivo', sa.String(length=500), nullable=True))


def downgrade():
    op.drop_column('morador', 'transferido_motivo')
    op.drop_column('residente', 'excluido_motivo')
