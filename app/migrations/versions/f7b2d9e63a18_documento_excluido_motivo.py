"""justificativa da exclusão lógica do documento; as exclusões de 17/09/2026 recebem a justificativa informada pela administração

Revision ID: f7b2d9e63a18
Revises: e6a1c4d92b75
"""
from alembic import op
import sqlalchemy as sa

revision = 'f7b2d9e63a18'
down_revision = 'e6a1c4d92b75'
branch_labels = None
depends_on = None

MOTIVO_1709 = "Exclusão realizada para substituir por mesmo documento ajustado a pedido do cartório pois faltou o CNPJ do condomínio."


def upgrade():
    op.add_column('documento', sa.Column('excluido_motivo', sa.String(length=500), nullable=True))
    op.execute(sa.text("update documento set excluido_motivo = :m where excluido_em is not null and excluido_motivo is null "
                       "and (excluido_em at time zone 'America/Belem')::date = date '2026-09-17'").bindparams(m=MOTIVO_1709))


def downgrade():
    op.drop_column('documento', 'excluido_motivo')
