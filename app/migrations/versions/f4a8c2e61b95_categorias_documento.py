"""categorias de documento em tabela

Revision ID: f4a8c2e61b95
Revises: e1c5d9b73a48
"""
from alembic import op
import sqlalchemy as sa

revision = 'f4a8c2e61b95'
down_revision = 'e1c5d9b73a48'
branch_labels = None
depends_on = None

INICIAIS = ["Convenção", "Regimento interno", "Atas de assembleia", "Balancetes", "Contratos com Administradora de Condomínio",
            "Contratos com Terceiros", "Comunicados", "Outros"]


def upgrade():
    t = op.create_table('categoria_documento',
        sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
        sa.Column('nome', sa.String(length=80), nullable=False),
        sa.Column('criado_em', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'), sa.UniqueConstraint('nome'))
    op.bulk_insert(t, [{"nome": n} for n in INICIAIS])


def downgrade():
    op.drop_table('categoria_documento')
